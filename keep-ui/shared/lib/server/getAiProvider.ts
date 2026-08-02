// server only!
import { auth } from "@/auth";
import { getApiURL } from "@/utils/apiUrl";

/**
 * Resolve the AI credential Keep's built-in assistants should use.
 *
 * Keep already ships AI providers (`anthropic`, `openai`, `deepseek`,
 * `gemini`, `ollama`, …) with an install UI and a secret manager. Reading
 * from there means an operator enables the workflow assistant, the incident
 * chat and the RCA agents by installing **one** provider — not by setting a
 * separate `OPEN_AI_API_KEY` env that nothing else knows about.
 *
 * Env vars stay as the fallback so existing deployments keep working.
 */

export interface ResolvedAiProvider {
  apiKey: string;
  /** OpenAI-compatible endpoint; undefined means api.openai.com. */
  baseURL?: string;
  model?: string;
  source: "keep-provider" | "environment";
  providerType?: string;
  providerId?: string;
}

/**
 * OpenAI SDKs expect the base URL to include the API version segment.
 * Operators routinely paste the bare host (`http://ollama:11434`), so add
 * the conventional `/v1` when it is missing rather than failing later with
 * an opaque 404.
 */
function normalizeBaseUrl(url?: string): string | undefined {
  if (!url) return undefined;
  const trimmed = url.replace(/\/+$/, "");
  if (/\/v\d+(\/.*)?$/.test(trimmed) || trimmed.includes("/openai")) return trimmed;
  return `${trimmed}/v1`;
}

/**
 * Default OpenAI-compatible endpoint per Keep provider type.
 *
 * Providers that carry their own endpoint (`api_url`) override this —
 * that is what makes the generic `openai_compatible` provider, LiteLLM,
 * vLLM and Ollama work without a code change per vendor.
 */
const BASE_URLS: Record<string, string | undefined> = {
  openai: undefined, // the SDK default
  deepseek: "https://api.deepseek.com",
  gemini: "https://generativelanguage.googleapis.com/v1beta/openai",
  grok: "https://api.x.ai/v1",
  // Anthropic exposes an OpenAI-compatible surface at /v1 too.
  anthropic: "https://api.anthropic.com/v1",
  openai_compatible: undefined, // always supplied by the provider
  litellm: undefined,
  vllm: undefined,
  ollama: undefined,
};

/** Sensible default model per provider when none is configured. */
const DEFAULT_MODELS: Record<string, string> = {
  openai: "gpt-4o",
  deepseek: "deepseek-v4-flash",
  gemini: "gemini-2.0-flash",
  anthropic: "claude-sonnet-4-5",
  grok: "grok-2-latest",
};

const AI_TYPES = Object.keys(BASE_URLS);

let cached: { at: number; value: ResolvedAiProvider | null } | null = null;
const CACHE_TTL_MS = 30_000;

function fromEnvironment(): ResolvedAiProvider | null {
  const apiKey = process.env.OPEN_AI_API_KEY || process.env.OPENAI_API_KEY;
  if (!apiKey) return null;
  return {
    apiKey,
    baseURL: process.env.OPEN_AI_BASE_URL || undefined,
    model: process.env.OPENAI_MODEL_NAME || undefined,
    source: "environment",
  };
}

async function fromKeepProvider(): Promise<ResolvedAiProvider | null> {
  try {
    const session = await auth();
    const token = (session as { accessToken?: string } | null)?.accessToken;
    if (!token) return null;

    const response = await fetch(`${getApiURL()}/providers`, {
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return null;

    const payload = await response.json();
    const installed: any[] = payload?.installed_providers ?? [];
    const provider = installed.find((item) => AI_TYPES.includes(item?.type));
    if (!provider) return null;

    const auth_ = provider?.details?.authentication ?? {};
    const apiKey: string = auth_.api_key || auth_.token || auth_.access_token || "";
    if (!apiKey) return null;

    return {
      apiKey,
      // The provider's own endpoint wins — that is how a self-hosted or
      // unlisted vendor works without adding code for it.
      baseURL: normalizeBaseUrl(auth_.api_url || auth_.host) ?? BASE_URLS[provider.type],
      // The model configured on the integration wins; otherwise a sane
      // default so the assistant works right after install.
      model: auth_.model || DEFAULT_MODELS[provider.type],
      source: "keep-provider",
      providerType: provider.type,
      providerId: provider.id,
    };
  } catch {
    // Keep unreachable, or no session: fall through to env.
    return null;
  }
}

/**
 * The credential to use, preferring an installed Keep provider.
 * Returns null when AI is genuinely not configured anywhere.
 */
export async function getAiProvider(): Promise<ResolvedAiProvider | null> {
  const now = Date.now();
  if (cached && now - cached.at < CACHE_TTL_MS) return cached.value;

  const value = (await fromKeepProvider()) ?? fromEnvironment();
  cached = { at: now, value };
  return value;
}

/** Whether any AI credential resolves — drives the UI feature flag. */
export async function isAiEnabled(): Promise<boolean> {
  return (await getAiProvider()) !== null;
}

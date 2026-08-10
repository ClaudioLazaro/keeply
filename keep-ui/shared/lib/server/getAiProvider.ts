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
  /** Operator's thinking-mode choice for this function; `auto` = work it out. */
  thinking?: ThinkingMode;
  /** Downgrades already learned for this model, applied without re-discovering. */
  knownDowngrades?: string[];
  /** Which AI feature this was resolved for. */
  function?: AssistantFunction;
}

/**
 * The AI features that route to a model. Each has genuinely different
 * needs — drafting a workflow wants fast and cheap, writing an RCA wants
 * the strongest model available — so each resolves independently.
 */
export type AssistantFunction =
  | "workflow_builder"
  | "incident_chat"
  | "ai_summary"
  | "rca";

export type ThinkingMode = "auto" | "on" | "off";

interface AssistantRouting {
  provider?: string;
  model?: string;
  thinking?: ThinkingMode;
  knownDowngrades?: string[];
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

// Keyed by function. A single shared entry would hand the first caller's
// provider to every other feature for the next 30 seconds — the exact bug
// this work exists to remove, just moved into the cache.
const cached = new Map<string, { at: number; value: ResolvedAiProvider | null }>();
const CACHE_TTL_MS = 30_000;

/** Drop cached resolutions — called after settings change, and by tests. */
export function invalidateAiProviderCache(): void {
  cached.clear();
}

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

/**
 * How the AI plane says this function should be routed.
 *
 * Best-effort on purpose. If the AIOps service is unreachable the builder
 * must still open — it degrades to the previous behaviour (tenant default,
 * then whatever AI provider is installed) rather than losing the assistant
 * because a *configuration* service is down.
 */
async function assistantRouting(
  fn: AssistantFunction
): Promise<AssistantRouting> {
  const base = process.env.AIOPS_API_URL || "http://localhost:8081";
  const key = process.env.AIOPS_API_KEY || "dev-key";
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/v1/config`, {
      headers: { "X-API-KEY": key, Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return {};
    const payload = await response.json();
    const entry = (payload?.assistants ?? []).find(
      (item: { function?: string }) => item?.function === fn
    );
    if (!entry) return {};
    return {
      provider: entry.provider || undefined,
      model: entry.model || undefined,
      thinking: entry.thinking || "auto",
      knownDowngrades: Array.isArray(entry.detected_downgrades)
        ? entry.detected_downgrades
        : [],
    };
  } catch {
    return {};
  }
}

async function fromKeepProvider(
  routing: AssistantRouting
): Promise<ResolvedAiProvider | null> {
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
    const candidates = installed.filter((item) => AI_TYPES.includes(item?.type));

    // The configured provider for this function wins. Without this the
    // choice was `candidates[0]` — whichever the API happened to return
    // first — so installing a second AI provider could silently move the
    // workflow builder onto a different model and nobody would know why
    // the answers changed.
    const provider =
      (routing.provider &&
        candidates.find((item) => item?.type === routing.provider)) ||
      candidates[0];
    if (!provider) return null;

    const auth_ = provider?.details?.authentication ?? {};
    const apiKey: string = auth_.api_key || auth_.token || auth_.access_token || "";
    if (!apiKey) return null;

    return {
      apiKey,
      // The provider's own endpoint wins — that is how a self-hosted or
      // unlisted vendor works without adding code for it.
      baseURL: normalizeBaseUrl(auth_.api_url || auth_.host) ?? BASE_URLS[provider.type],
      // This function's own model wins, then the provider integration's,
      // then a sane default so the assistant works right after install.
      model: routing.model || auth_.model || DEFAULT_MODELS[provider.type],
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
export async function getAiProvider(
  fn: AssistantFunction = "workflow_builder"
): Promise<ResolvedAiProvider | null> {
  const now = Date.now();
  const hit = cached.get(fn);
  if (hit && now - hit.at < CACHE_TTL_MS) return hit.value;

  const routing = await assistantRouting(fn);
  const resolved = (await fromKeepProvider(routing)) ?? fromEnvironment();
  const value: ResolvedAiProvider | null = resolved
    ? {
        ...resolved,
        function: fn,
        thinking: routing.thinking ?? "auto",
        knownDowngrades: routing.knownDowngrades ?? [],
        // An env-configured model is still overridable per function; that
        // is the whole point of the setting existing.
        model: routing.model || resolved.model,
      }
    : null;

  cached.set(fn, { at: now, value });
  return value;
}

/** Whether any AI credential resolves — drives the UI feature flag. */
export async function isAiEnabled(): Promise<boolean> {
  return (await getAiProvider()) !== null;
}

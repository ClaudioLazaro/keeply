import {
  CopilotRuntime,
  OpenAIAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import OpenAI, { OpenAIError } from "openai";
import { NextRequest } from "next/server";
import {
  getAiProvider,
  invalidateAiProviderCache,
  type AssistantFunction,
  type ResolvedAiProvider,
} from "@/shared/lib/server/getAiProvider";

const ASSISTANT_FUNCTIONS: AssistantFunction[] = [
  "workflow_builder",
  "incident_chat",
  "ai_summary",
  "rca",
];
import {
  openAiCompatFetch,
  type Downgrade,
} from "@/shared/lib/server/openAiCompatFetch";

/**
 * Persist what a refusal taught us, so the next request starts there.
 *
 * Fire-and-forget: the chat has already recovered by the time this runs,
 * and failing to record a lesson must never fail the conversation that
 * learned it. The worst case is rediscovering it next time.
 */
function reportCapability(
  resolved: ResolvedAiProvider,
  downgrades: Downgrade[],
  evidence: string
): void {
  const base = process.env.AIOPS_API_URL || "http://localhost:8081";
  const key = process.env.AIOPS_API_KEY || "dev-key";
  fetch(`${base.replace(/\/$/, "")}/v1/config/llm-capabilities`, {
    method: "POST",
    headers: { "X-API-KEY": key, "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: resolved.providerType,
      model: resolved.model,
      downgrades,
      evidence,
    }),
  })
    // Cached routing now carries stale knownDowngrades; drop it so the
    // next request picks up what was just learned.
    .then(() => invalidateAiProviderCache())
    .catch((error) => {
      console.warn("could not record model capability", error);
    });
}

export const POST = async (req: NextRequest) => {
  // Credentials come from the AI provider installed in Keep, falling back
  // to OPEN_AI_API_KEY / OPENAI_API_KEY. `baseURL` lets any
  // OpenAI-compatible provider drive the assistant — DeepSeek, Gemini,
  // Grok, a local Ollama — not only api.openai.com.
  // Which AI feature is asking. Each call site declares itself, so the
  // builder and the incident chat can run different models. An unknown or
  // absent value falls back to the builder rather than erroring — a stale
  // client should still get an assistant.
  const requested = req.nextUrl.searchParams.get("fn");
  const fn: AssistantFunction = ASSISTANT_FUNCTIONS.includes(
    requested as AssistantFunction
  )
    ? (requested as AssistantFunction)
    : "workflow_builder";

  const provider = await getAiProvider(fn);

  if (!provider) {
    return new Response(
      "No AI provider configured. Install one under Providers.",
      { status: 503 }
    );
  }

  // Passed in rather than closed over: TypeScript does not carry the
  // null-narrowing above into a hoisted function declaration.
  function initializeCopilotRuntime(resolved: ResolvedAiProvider) {
    try {
      const openai = new OpenAI({
        organization: process.env.OPEN_AI_ORGANIZATION_ID,
        apiKey: resolved.apiKey,
        ...(resolved.baseURL
          ? {
              baseURL: resolved.baseURL,
              // Providers that only speak the OpenAI wire format reject
              // several things CopilotKit sends unconditionally. Rather
              // than assume which, the shim starts from what this model
              // already taught us and discovers the rest from real
              // refusals — see openAiCompatFetch.
              fetch: openAiCompatFetch(undefined, {
                thinking: resolved.thinking ?? "auto",
                known: (resolved.knownDowngrades ?? []) as Downgrade[],
                onLearned: (downgrades, evidence) =>
                  reportCapability(resolved, downgrades, evidence),
              }),
            }
          : {}),
      });
      const serviceAdapter = new OpenAIAdapter({
        openai,
        ...(resolved.model ? { model: resolved.model } : {}),
      });
      const runtime = new CopilotRuntime();
      return { runtime, serviceAdapter };
    } catch (error) {
      if (error instanceof OpenAIError) {
        console.log("Error connecting to the AI provider", error);
      } else {
        console.error("Error initializing Copilot Runtime", error);
      }
      return null;
    }
  }

  const runtimeOptions = initializeCopilotRuntime(provider);

  if (!runtimeOptions) {
    return new Response("Error initializing Copilot Runtime", { status: 500 });
  }
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime: runtimeOptions.runtime,
    serviceAdapter: runtimeOptions.serviceAdapter,
    endpoint: "/api/copilotkit",
  });

  return handleRequest(req);
};

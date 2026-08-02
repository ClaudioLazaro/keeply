import {
  CopilotRuntime,
  OpenAIAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import OpenAI, { OpenAIError } from "openai";
import { NextRequest } from "next/server";
import {
  getAiProvider,
  type ResolvedAiProvider,
} from "@/shared/lib/server/getAiProvider";

export const POST = async (req: NextRequest) => {
  // Credentials come from the AI provider installed in Keep, falling back
  // to OPEN_AI_API_KEY / OPENAI_API_KEY. `baseURL` lets any
  // OpenAI-compatible provider drive the assistant — DeepSeek, Gemini,
  // Grok, a local Ollama — not only api.openai.com.
  const provider = await getAiProvider();

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
        ...(resolved.baseURL ? { baseURL: resolved.baseURL } : {}),
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

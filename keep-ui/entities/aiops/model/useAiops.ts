"use client";

import useSWR, { SWRConfiguration } from "swr";
import {
  AIOPS_PROXY_BASE_PATH,
  INVESTIGATION_POLL_INTERVAL_MS,
  isInvestigationInFlight,
} from "@/entities/investigation/model/useInvestigation";
import { Investigation } from "@/entities/investigation/model/types";
import { useState } from "react";
import { useSWRConfig } from "swr";
import { aiopsKeys } from "../lib/aiopsKeys";
import {
  AgentConfig,
  AgentConfigUpdate,
  AiopsStats,
  Integration,
  LlmProvider,
  Policy,
  PolicyRule,
  ToolCatalogResponse,
} from "./types";

// Every call goes through the Next.js server proxy at /api/aiops/*, which
// attaches AIOPS_API_KEY upstream. The browser never holds the credential
// and never reaches aiops-api (or the MCP gateway) directly.
async function aiopsGet<T>(path: string): Promise<T> {
  const response = await fetch(`${AIOPS_PROXY_BASE_PATH}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`aiops request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export function useAiopsStats(swrConfig?: SWRConfiguration<AiopsStats>) {
  const { data, error, isLoading } = useSWR<AiopsStats>(
    aiopsKeys.stats(),
    () => aiopsGet<AiopsStats>("/v1/stats"),
    { refreshInterval: 15000, ...swrConfig }
  );
  return { stats: data, isLoading: isLoading && !data, error };
}

export function useAiopsTools(swrConfig?: SWRConfiguration<ToolCatalogResponse>) {
  const { data, error, isLoading } = useSWR<ToolCatalogResponse>(
    aiopsKeys.tools(),
    () => aiopsGet<ToolCatalogResponse>("/v1/tools"),
    swrConfig
  );
  return { catalog: data, isLoading: isLoading && !data, error };
}

export function useAiopsPolicies(swrConfig?: SWRConfiguration<Policy[]>) {
  const { data, error, isLoading } = useSWR<Policy[]>(
    aiopsKeys.policies(),
    () => aiopsGet<Policy[]>("/v1/policies"),
    swrConfig
  );
  return { policies: data, isLoading: isLoading && !data, error };
}

/**
 * Every investigation visible to the tenant, newest first.
 *
 * aiops-api returns them oldest-first (`order_by(created_at)`); the console
 * wants the most recent work at the top, so the order is reversed here
 * rather than adding a query param the API does not have.
 */
export function useAiopsInvestigations(
  swrConfig?: SWRConfiguration<Investigation[]>
) {
  const { data, error, isLoading } = useSWR<Investigation[]>(
    aiopsKeys.investigations(),
    () => aiopsGet<Investigation[]>("/v1/investigations"),
    {
      // Keep polling while anything is still running so the list is live.
      refreshInterval: (latest) =>
        latest?.some(isInvestigationInFlight)
          ? INVESTIGATION_POLL_INTERVAL_MS
          : 0,
      ...swrConfig,
    }
  );

  const investigations = data ? [...data].reverse() : undefined;

  return { investigations, isLoading: isLoading && !data, error };
}

export function useAgentConfig(swrConfig?: SWRConfiguration<AgentConfig>) {
  const { data, error, isLoading } = useSWR<AgentConfig>(
    aiopsKeys.config(),
    () => aiopsGet<AgentConfig>("/v1/config"),
    swrConfig
  );
  return { config: data, isLoading: isLoading && !data, error };
}

export function useLlmProviders() {
  const { data } = useSWR<{ providers: LlmProvider[] }>(
    aiopsKeys.llmProviders(),
    () => aiopsGet<{ providers: LlmProvider[] }>("/v1/config/llm-providers"),
    { revalidateOnFocus: false }
  );
  return { providers: data?.providers ?? [] };
}

export interface UpdateAgentConfigResult {
  ok: boolean;
  /** Server-side validation message, ready to show. */
  error?: string;
}

/**
 * Persist a partial agent-config update.
 *
 * The 422 body from the API is already redacted server-side, so surfacing
 * its message cannot leak a pasted credential.
 */
export function useAgentConfigActions() {
  const { mutate } = useSWRConfig();
  const [isSaving, setIsSaving] = useState(false);

  async function save(update: AgentConfigUpdate): Promise<UpdateAgentConfigResult> {
    setIsSaving(true);
    try {
      const response = await fetch(`${AIOPS_PROXY_BASE_PATH}/v1/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(update),
      });

      if (!response.ok) {
        let message = `Save failed (HTTP ${response.status})`;
        try {
          const body = await response.json();
          const detail = body?.detail;
          if (Array.isArray(detail) && detail[0]?.msg) {
            message = detail[0].msg;
          } else if (typeof detail === "string") {
            message = detail;
          }
        } catch {
          // Non-JSON error body: keep the status-based message.
        }
        return { ok: false, error: message };
      }

      await mutate(aiopsKeys.config());
      // Budget/model changes move the numbers the overview reports.
      await mutate(aiopsKeys.stats());
      return { ok: true };
    } catch (error) {
      return { ok: false, error: "Could not reach the AIOps control plane." };
    } finally {
      setIsSaving(false);
    }
  }

  return { save, isSaving };
}

export interface LlmTestResult {
  ok: boolean;
  detail: string;
  models: string[];
  model_tested: string | null;
}

/**
 * Probe the provider with a real completion.
 *
 * No credential is sent: it comes from the installed Keep provider, so the
 * browser never handles a key.
 */
export function useLlmConnectionTest() {
  const [isTesting, setIsTesting] = useState(false);
  const [result, setResult] = useState<LlmTestResult | null>(null);

  async function test(input: { llm_model?: string }) {
    setIsTesting(true);
    try {
      const response = await fetch(`${AIOPS_PROXY_BASE_PATH}/v1/config/llm:test`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(input),
      });
      const body = (await response.json()) as LlmTestResult;
      setResult(body);
      return body;
    } catch {
      const failure: LlmTestResult = {
        ok: false,
        detail: "Could not reach the AIOps control plane.",
        models: [],
        model_tested: null,
      };
      setResult(failure);
      return failure;
    } finally {
      setIsTesting(false);
    }
  }

  return { test, isTesting, result, clear: () => setResult(null) };
}

export interface SavePolicyResult {
  ok: boolean;
  error?: string;
}

/** Upsert a policy (PUT /v1/policies/{id}). */
export function usePolicyActions() {
  const { mutate } = useSWRConfig();
  const [isSaving, setIsSaving] = useState(false);

  async function savePolicy(
    policyId: string,
    body: {
      tenant_id: string;
      description: string;
      rules: PolicyRule[];
      enabled: boolean;
    }
  ): Promise<SavePolicyResult> {
    setIsSaving(true);
    try {
      const response = await fetch(
        `${AIOPS_PROXY_BASE_PATH}/v1/policies/${encodeURIComponent(policyId)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(body),
        }
      );
      if (!response.ok) {
        let message = `Save failed (HTTP ${response.status})`;
        try {
          const payload = await response.json();
          const detail = payload?.detail;
          if (Array.isArray(detail) && detail[0]?.msg) message = detail[0].msg;
          else if (typeof detail === "string") message = detail;
        } catch {
          // keep the status-based message
        }
        return { ok: false, error: message };
      }
      await mutate(aiopsKeys.policies());
      // A policy change can flip a tool from allow to deny.
      await mutate(aiopsKeys.tools());
      return { ok: true };
    } catch {
      return { ok: false, error: "Could not reach the AIOps control plane." };
    } finally {
      setIsSaving(false);
    }
  }

  return { savePolicy, isSaving };
}

export function useIntegrations(swrConfig?: SWRConfiguration<Integration[]>) {
  const { data, error, isLoading } = useSWR<Integration[]>(
    aiopsKeys.integrations(),
    () => aiopsGet<Integration[]>("/v1/integrations"),
    swrConfig
  );
  return { integrations: data, isLoading: isLoading && !data, error };
}


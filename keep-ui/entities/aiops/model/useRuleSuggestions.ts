"use client";

import { useState } from "react";
import useSWR, { SWRConfiguration, useSWRConfig } from "swr";
import { AIOPS_PROXY_BASE_PATH } from "@/entities/investigation/model/useInvestigation";
import { aiopsKeys } from "../lib/aiopsKeys";

/**
 * Correlation rules the analysis proposes.
 *
 * The AI plane never creates incidents — Keep's rules engine does, on the
 * ingestion path. These are proposals for that engine, waiting on a human
 * decision.
 */
export interface RuleSuggestion {
  id: string;
  name: string;
  cel: string;
  grouping_criteria: string[];
  timeframe_seconds: number;
  /** How many times the pattern recurred — the evidence for the rule. */
  occurrences: number;
  alerts_covered: number;
  rationale: string;
  status: "pending" | "accepted" | "dismissed";
  created_rule_id: string | null;
  created_at: string;
}

export function useRuleSuggestions(
  status: string = "pending",
  swrConfig?: SWRConfiguration<RuleSuggestion[]>
) {
  const { data, error, isLoading } = useSWR<RuleSuggestion[]>(
    [aiopsKeys.all, "rule-suggestions", status].join("::"),
    async () => {
      const response = await fetch(
        `${AIOPS_PROXY_BASE_PATH}/v1/correlation/suggestions?status=${encodeURIComponent(status)}`,
        { headers: { Accept: "application/json" } }
      );
      if (!response.ok) {
        throw new Error(`aiops request failed with status ${response.status}`);
      }
      return (await response.json()) as RuleSuggestion[];
    },
    // The AIOps control plane is optional; a deployment without it must
    // still show the rules page normally rather than an error.
    { shouldRetryOnError: false, ...swrConfig }
  );

  return { suggestions: data, isLoading: isLoading && !data, error };
}

export interface SuggestionActionResult {
  ok: boolean;
  error?: string;
  ruleId?: string;
}

export function useRuleSuggestionActions() {
  const { mutate } = useSWRConfig();
  const [pendingId, setPendingId] = useState<string | null>(null);

  async function act(
    suggestionId: string,
    action: "accept" | "dismiss"
  ): Promise<SuggestionActionResult> {
    setPendingId(suggestionId);
    try {
      const response = await fetch(
        `${AIOPS_PROXY_BASE_PATH}/v1/correlation/suggestions/${encodeURIComponent(
          suggestionId
        )}:${action}`,
        { method: "POST", headers: { Accept: "application/json" } }
      );
      if (!response.ok) {
        let message = `Request failed (HTTP ${response.status})`;
        try {
          const body = await response.json();
          if (typeof body?.detail === "string") message = body.detail;
        } catch {
          // keep the status-based message
        }
        return { ok: false, error: message };
      }
      const body = await response.json();
      // Accepting creates a real rule, so the rules list changes too.
      await mutate(
        (key) =>
          typeof key === "string" &&
          (key.includes("rule-suggestions") || key.includes("/rules"))
      );
      return { ok: true, ruleId: body?.rule_id };
    } catch {
      return { ok: false, error: "Could not reach the AIOps control plane." };
    } finally {
      setPendingId(null);
    }
  }

  return {
    accept: (id: string) => act(id, "accept"),
    dismiss: (id: string) => act(id, "dismiss"),
    pendingId,
  };
}

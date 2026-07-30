"use client";

import useSWR, { SWRConfiguration } from "swr";
import { investigationKeys } from "../lib/investigationKeys";
import { AIOPS_PROXY_BASE_PATH } from "./useInvestigation";
import { InvestigationFeedback } from "./types";

async function aiopsGetFeedback(
  investigationId: string
): Promise<InvestigationFeedback | undefined> {
  const response = await fetch(
    `${AIOPS_PROXY_BASE_PATH}/investigations/${encodeURIComponent(
      investigationId
    )}/feedback`,
    { headers: { Accept: "application/json" } }
  );
  // No feedback submitted yet is a normal state, not an error.
  if (response.status === 404) {
    return undefined;
  }
  if (!response.ok) {
    throw new Error(`aiops request failed with status ${response.status}`);
  }
  return (await response.json()) as InvestigationFeedback;
}

export interface UseInvestigationFeedbackValue {
  feedback: InvestigationFeedback | undefined;
  isLoading: boolean;
  error: unknown;
}

export function useInvestigationFeedback(
  investigationId: string | undefined,
  swrConfig?: SWRConfiguration<InvestigationFeedback | undefined>
): UseInvestigationFeedbackValue {
  const { data, error, isLoading } = useSWR<
    InvestigationFeedback | undefined
  >(
    investigationId ? investigationKeys.feedback(investigationId) : null,
    () => aiopsGetFeedback(investigationId!),
    swrConfig
  );

  return { feedback: data, isLoading: isLoading && !data, error };
}

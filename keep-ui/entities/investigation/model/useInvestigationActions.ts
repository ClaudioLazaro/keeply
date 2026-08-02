"use client";

import { useCallback, useState } from "react";
import { useSWRConfig } from "swr";
import { showErrorToast, showSuccessToast } from "@/shared/ui";
import { investigationKeys } from "../lib/investigationKeys";
import { AIOPS_PROXY_BASE_PATH } from "./useInvestigation";
import {
  InvestigationFeedback,
  InvestigationFeedbackRating,
} from "./types";

export interface UseInvestigationFeedbackActionsValue {
  submitFeedback: (
    investigationId: string,
    rating: InvestigationFeedbackRating,
    comment?: string | null
  ) => Promise<InvestigationFeedback | null>;
  isSubmitting: boolean;
}

export function useInvestigationFeedbackActions(): UseInvestigationFeedbackActionsValue {
  const { mutate } = useSWRConfig();
  const [isSubmitting, setIsSubmitting] = useState(false);

  const submitFeedback = useCallback(
    async (
      investigationId: string,
      rating: InvestigationFeedbackRating,
      comment: string | null = null
    ): Promise<InvestigationFeedback | null> => {
      setIsSubmitting(true);
      try {
        const response = await fetch(
          `${AIOPS_PROXY_BASE_PATH}/v1/investigations/${encodeURIComponent(
            investigationId
          )}/feedback`,
          {
            method: "POST",
            headers: {
              Accept: "application/json",
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ rating, comment }),
          }
        );
        if (!response.ok) {
          throw new Error(
            `aiops request failed with status ${response.status}`
          );
        }
        const feedback = (await response.json()) as InvestigationFeedback;
        await mutate(investigationKeys.feedback(investigationId), feedback, {
          revalidate: false,
        });
        showSuccessToast("Thanks for your feedback!");
        return feedback;
      } catch (error) {
        showErrorToast(error, "Failed to submit feedback");
        return null;
      } finally {
        setIsSubmitting(false);
      }
    },
    [mutate]
  );

  return { submitFeedback, isSubmitting };
}

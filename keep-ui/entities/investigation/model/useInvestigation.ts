"use client";

import useSWR, { SWRConfiguration } from "swr";
import { investigationKeys } from "../lib/investigationKeys";
import {
  Investigation,
  InvestigationEvidence,
  InvestigationHypothesis,
  InvestigationStatus,
} from "./types";

// Client-side calls go through the Next.js server proxy at /api/aiops/*,
// which attaches AIOPS_API_KEY upstream (never exposed to the browser).
export const AIOPS_PROXY_BASE_PATH = "/api/aiops";

export const INVESTIGATION_POLL_INTERVAL_MS = 3000;

export const IN_FLIGHT_INVESTIGATION_STATUSES: InvestigationStatus[] = [
  "queued",
  "gathering",
  "hypothesizing",
];

export function isInvestigationInFlight(
  investigation: Investigation | undefined | null
): boolean {
  return (
    !!investigation &&
    IN_FLIGHT_INVESTIGATION_STATUSES.includes(investigation.status)
  );
}

async function aiopsGet<T>(path: string): Promise<T> {
  const response = await fetch(`${AIOPS_PROXY_BASE_PATH}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`aiops request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export interface UseInvestigationByIncidentValue {
  investigation: Investigation | undefined;
  isInFlight: boolean;
  isLoading: boolean;
  error: unknown;
}

export function useInvestigationByIncident(
  incidentId: string | undefined,
  swrConfig?: SWRConfiguration<Investigation[]>
): UseInvestigationByIncidentValue {
  const { data, error, isLoading } = useSWR<Investigation[]>(
    incidentId ? investigationKeys.byIncident(incidentId) : null,
    () =>
      aiopsGet<Investigation[]>(
        `/investigations?incident_id=${encodeURIComponent(incidentId!)}`
      ),
    {
      // Poll only while any investigation for this incident is still running.
      refreshInterval: (latestData) =>
        latestData?.some(isInvestigationInFlight)
          ? INVESTIGATION_POLL_INTERVAL_MS
          : 0,
      ...swrConfig,
    }
  );

  const investigation = data?.[0];

  return {
    investigation,
    isInFlight: isInvestigationInFlight(investigation),
    isLoading: isLoading && !data,
    error,
  };
}

export interface UseInvestigationEvidenceValue {
  evidence: InvestigationEvidence[] | undefined;
  isLoading: boolean;
  error: unknown;
}

export function useInvestigationEvidence(
  investigationId: string | undefined,
  swrConfig?: SWRConfiguration<InvestigationEvidence[]>
): UseInvestigationEvidenceValue {
  const { data, error, isLoading } = useSWR<InvestigationEvidence[]>(
    investigationId ? investigationKeys.evidence(investigationId) : null,
    () =>
      aiopsGet<InvestigationEvidence[]>(
        `/investigations/${encodeURIComponent(investigationId!)}/evidence`
      ),
    swrConfig
  );

  return { evidence: data, isLoading: isLoading && !data, error };
}

export interface UseInvestigationHypothesesValue {
  hypotheses: InvestigationHypothesis[] | undefined;
  isLoading: boolean;
  error: unknown;
}

export function useInvestigationHypotheses(
  investigationId: string | undefined,
  swrConfig?: SWRConfiguration<InvestigationHypothesis[]>
): UseInvestigationHypothesesValue {
  const { data, error, isLoading } = useSWR<InvestigationHypothesis[]>(
    investigationId ? investigationKeys.hypotheses(investigationId) : null,
    () =>
      aiopsGet<InvestigationHypothesis[]>(
        `/investigations/${encodeURIComponent(investigationId!)}/hypotheses`
      ),
    swrConfig
  );

  return { hypotheses: data, isLoading: isLoading && !data, error };
}

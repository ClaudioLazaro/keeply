// Mirrors the aiops-api JSON payloads (keep-aiops/aiops_api).

export type InvestigationStatus =
  | "queued"
  | "gathering"
  | "hypothesizing"
  | "rca_ready"
  | "failed"
  | "cancelled";

export interface Investigation {
  id: string;
  tenant_id: string;
  incident_id: string;
  status: InvestigationStatus;
  mode?: string;
  rca_draft: string | null;
  error: string | null;
  created_at: string;
  updated_at?: string;
}

export interface InvestigationEvidence {
  id: string;
  investigation_id: string;
  tool: string;
  summary: string;
  /**
   * Provenance: "live" = real system, "stub" = canned demo payload,
   * "gap" = the call failed. Optional because rows written before the
   * column existed have none.
   */
  backend?: "live" | "stub" | "gap" | "unknown";
  created_at: string;
}

export type InvestigationFeedbackRating = "useful" | "not_useful";

export interface InvestigationFeedback {
  id: string;
  investigation_id: string;
  tenant_id: string;
  rating: InvestigationFeedbackRating;
  comment: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvestigationHypothesis {
  id: string;
  investigation_id?: string;
  title: string;
  /** Already discounted server-side when `corroborated` is false. */
  confidence: number;
  supporting_evidence: string[];
  supporting_knowledge: string[];
  /** False when no live evidence backs this hypothesis. */
  corroborated?: boolean;
  /** Why it is unverified, when it is. */
  caveat?: string;
  created_at?: string;
}

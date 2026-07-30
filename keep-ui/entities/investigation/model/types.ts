// Mirrors the aiops-api JSON payloads (keep-aiops/aiops_api).

export type InvestigationStatus =
  | "queued"
  | "gathering"
  | "hypothesizing"
  | "rca_ready"
  | "failed";

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
  confidence: number;
  supporting_evidence: string[];
  supporting_knowledge: string[];
  created_at?: string;
}

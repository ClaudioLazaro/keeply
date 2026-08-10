// Mirrors the aiops-api console payloads
// (keep-aiops/aiops_api/modules/{tools,stats,policy}).

export type PolicyDecision = "allow" | "deny" | "approval_required";

/**
 * Where a tool's data comes from. "stub" means canned demo payloads —
 * indistinguishable from real telemetry once rendered, which is exactly
 * why it has to be shown.
 */
export type ToolMode = "live" | "stub" | "unknown";

export interface ToolCatalogEntry {
  name: string;
  description: string;
  execution_class: string;
  input_schema: Record<string, unknown>;
  mode: ToolMode;
  /** Effective policy outcome for this tool right now. */
  decision: PolicyDecision;
  /** null when the fail-closed default produced the decision. */
  policy_id: string | null;
}

export interface ToolCatalogResponse {
  gateway_url: string;
  /** false = catalog could not be fetched; `tools` is empty and `error` says why. */
  gateway_available: boolean;
  tools: ToolCatalogEntry[];
  error: string | null;
}

export interface BudgetLimits {
  max_tool_calls: number;
  max_wall_time_seconds: number;
  max_llm_tokens: number;
}

export interface AiopsStats {
  investigations_total: number;
  investigations_by_status: Record<string, number>;
  investigations_last_24h: number;
  evidence_total: number;
  evidence_gaps: number;
  /** live | stub | gap | unknown — the composition an operator reads before
   *  trusting any hypothesis. */
  evidence_by_provenance: Record<string, number>;
  investigations_daily: { date: string; count: number }[];
  feedback_useful: number;
  feedback_not_useful: number;
  budget: BudgetLimits;
  mode: string;
  llm_enabled: boolean;
  llm_spend: {
    usd: number;
    priced_completions: number;
    /** Non-zero means `usd` is an underestimate, not a cheap month. */
    unpriced_completions: number;
  };
}

export interface PolicyRule {
  execution_class: string;
  decision: PolicyDecision;
  tools: string[];
  environments: string[];
}

export interface Policy {
  id: string;
  tenant_id: string;
  description: string | null;
  rules: PolicyRule[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/** Presence and origin of the credential — never the value itself. */
export interface LlmKeyStatus {
  env_var: string | null;
  present: boolean;
  source: string;
  /** Last 4 characters only, so a key can be identified but not rebuilt. */
  masked: string;
  /** The installed Keep provider supplying the credential, if any. */
  provider_id: string | null;
  provider_type: string | null;
}

export type ThinkingMode = "auto" | "on" | "off";

/**
 * One AI feature, as resolved for the settings page.
 *
 * `inherited` and `detected_downgrades` are deliberately separate kinds of
 * fact: the first says which values fell through to a default rather than
 * being chosen, the second says what the system worked out on its own by
 * being refused. Rendering them the same way would tell the operator they
 * configured something they never touched.
 */
export interface AssistantView {
  function: string;
  purpose: string;
  provider: string | null;
  /** The specific installation, when several of one type are installed. */
  provider_id: string | null;
  model: string | null;
  thinking: ThinkingMode;
  /** Field names that came from a default rather than this function. */
  inherited: string[];
  /** Compatibility workarounds learned by being refused. */
  detected_downgrades: string[];
  /** The provider's verbatim refusal — the cause on record. */
  detected_evidence: string | null;
}

export interface AssistantUpdate {
  provider?: string | null;
  provider_id?: string | null;
  model?: string | null;
  thinking?: ThinkingMode;
}

export interface AgentConfig {
  tenant_id: string;
  assistants: AssistantView[];
  available_thinking_modes: ThinkingMode[];
  llm_provider: string | null;
  llm_model: string | null;
  llm_enabled: boolean;
  llm_api_key: LlmKeyStatus;
  budget_max_tool_calls: number;
  budget_max_wall_time_seconds: number;
  budget_max_llm_tokens: number;
  context_timeline_limit: number;
  llm_embedding_model: string | null;
  auto_investigate_severities: string[];
  disabled_specialists: string[];
  available_specialists: string[];
  available_severities: string[];
}

/** Partial update: omitted fields are untouched, explicit null resets to env. */
export interface AgentConfigUpdate {
  llm_provider?: string | null;
  llm_model?: string | null;
  llm_api_key_env?: string | null;
  budget_max_tool_calls?: number | null;
  budget_max_wall_time_seconds?: number | null;
  budget_max_llm_tokens?: number | null;
  context_timeline_limit?: number | null;
  llm_embedding_model?: string | null;
  auto_investigate_severities?: string[] | null;
  disabled_specialists?: string[] | null;
  /** Merged per function server-side, so one card can be saved at a time. */
  assistants?: Record<string, AssistantUpdate>;
}

/** An AI provider installed in Keep, offered for LLM routing. */
export interface LlmProvider {
  id: string;
  type: string;
  label: string;
  configured: boolean;
  suggested_model: string;
}

export interface Integration {
  name: string;
  label: string;
  mode: ToolMode;
  tools: string[];
  notes: string;
  /** The installed Keep provider backing this integration, if any. */
  provider: { id: string; type: string; display_name: string } | null;
  /** Keep provider types that can back it, for the install link. */
  provider_types: string[];
  /** True for backends using ambient credentials (K8s SA, AWS chain). */
  ambient_credentials: boolean;
}

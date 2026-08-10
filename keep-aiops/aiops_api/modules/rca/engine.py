"""RCA generation engine: LiteLLM path with deterministic fallback.

LLM is OPTIONAL at runtime: when AIOPS_LLM_MODEL is empty (or the LLM call /
response parsing fails) the deterministic rule-based fallback produces the
draft instead. Every generated draft carries [E#]/[K#] citations resolvable
via the returned citations map and the suggest-only disclaimer.
"""

import json
import logging
import re
from typing import Any

from aiops_api.modules.rca.draft import (
    DETAIL_MAX_CHARS,
    DETAIL_TOTAL_MAX_CHARS,
    build_citations,
    evidence_detail,
    item_field,
    item_id,
    render_draft,
)
from aiops_api.modules.rca.fallback import deterministic_rca
from aiops_api.modules.rca.provenance import annotate_hypotheses
from aiops_api.modules.rca.schemas import LlmRcaResponse
from aiops_api.settings import Settings, get_settings
from aiops_api.telemetry import investigation_span

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a root-cause analysis assistant for site-reliability engineers.

You are given an incident, gathered read-only evidence labelled [E1], [E2], ...,
and knowledge-base documents labelled [K1], [K2], ...

Respond with STRICT JSON only (no markdown fences, no commentary) matching:
{
  "summary": "one-paragraph incident summary",
  "hypotheses": [
    {
      "title": "short noun-phrase root-cause hypothesis",
      "confidence": 0.0-1.0,
      "evidence_refs": ["E1"],
      "knowledge_refs": ["K1"]
    }
  ]
}

Rules:
- Rank hypotheses by likelihood; each MUST cite at least one [E#] evidence ref.
- Only use the [E#]/[K#] labels provided — never invent ids.
- This analysis is suggest-only: NEVER propose or describe remediation actions
  (no restarts, rollouts, scaling, or kubectl commands). State causes, not fixes.
"""

_REF_NORMALIZER = re.compile(r"^\[?\s*([eEkK]\d+)\s*\]?$")

# Ceiling for one RCA completion. Generous on purpose: reasoning models
# bill their hidden chain-of-thought against the same completion budget,
# so a tight limit starves the actual JSON answer. The per-investigation
# token budget (AIOPS_BUDGET_MAX_LLM_TOKENS) is the real cost guard.
RCA_MAX_TOKENS = 8000


def _incident_view(investigation: Any, context_pack: dict | None) -> dict:
    """Incident dict for prompts/rendering; context pack fields win when present."""
    incident: dict[str, Any] = {
        "id": investigation.incident_id,
        "tenant_id": investigation.tenant_id,
        "investigation_id": investigation.id,
    }
    if isinstance(context_pack, dict):
        pack_incident = context_pack.get("incident")
        if isinstance(pack_incident, dict):
            incident.update({k: v for k, v in pack_incident.items() if v is not None})
    # Identity fields always come from the investigation — never trust the pack.
    incident["id"] = investigation.incident_id
    incident["tenant_id"] = investigation.tenant_id
    incident["investigation_id"] = investigation.id
    return incident


def _normalize_ref(raw: str) -> str | None:
    match = _REF_NORMALIZER.match(str(raw).strip())
    if match is None:
        return None
    return match.group(1).upper()


def _llm_user_prompt(
    incident: dict, evidence: list[Any], knowledge: list[Any], citations: dict[str, dict[str, str]]
) -> str:
    evidence_labels = {v: k for k, v in citations["evidence"].items()}
    knowledge_labels = {v: k for k, v in citations["knowledge"].items()}
    lines = [f"Incident: {json.dumps(incident, default=str)}", "", "Evidence:"]
    # Budgeted across all items: the earliest evidence is the most relevant
    # (specialists run in priority order), so exhausting the budget degrades
    # to summary-only for the tail rather than truncating the head.
    remaining = DETAIL_TOTAL_MAX_CHARS
    for item in evidence:
        marker = evidence_labels.get(item_id(item), "?")
        lines.append(f"[{marker}] {item_field(item, 'tool')}: {item_field(item, 'summary')}")
        if remaining > 0:
            detail = evidence_detail(item, max_chars=min(DETAIL_MAX_CHARS, remaining))
            if detail:
                lines.append(f"      {detail}")
                remaining -= len(detail)
    lines.append("")
    lines.append("Knowledge documents:")
    for item in knowledge:
        marker = knowledge_labels.get(item_id(item), "?")
        chunk = str(item_field(item, "chunk"))[:500]
        lines.append(f"[{marker}] {item_field(item, 'title')}: {chunk}")
    return "\n".join(lines)


def _call_llm(settings: Settings, incident: dict, evidence: list[Any], knowledge: list[Any],
              citations: dict[str, dict[str, str]]) -> tuple[LlmRcaResponse, int]:
    """One LiteLLM completion round-trip; raises on any failure.

    Returns ``(parsed, total_tokens)`` so the orchestrator can charge the
    per-investigation cost budget. ``total_tokens`` is the LiteLLM
    ``usage.total_tokens`` when reported, else 0 (the budget treats 0 as
    free, which is the safe failure mode for unknown providers).
    """
    import litellm  # lazy: the no-LLM path must not pay the import cost

    # Model/credential come from the effective agent config (persisted row
    # over env), so changing the provider in the UI takes effect without a
    # redeploy.
    from aiops_api.modules.config import get_effective_config, model_for

    config = get_effective_config(getattr(incident, "tenant_id", None) or incident.get("tenant_id", "*"))
    # Resolved once, through the one place that applies both the
    # per-function override and LiteLLM's spelling.
    rca_model = model_for(config, "rca", settings)

    response = litellm.completion(
        model=rca_model,
        api_key=config.llm_api_key or None,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _llm_user_prompt(incident, evidence, knowledge, citations)},
        ],
        temperature=0,
        # Reasoning models (DeepSeek v4, o-series, …) spend completion
        # tokens on hidden reasoning BEFORE emitting `content`. Too small a
        # ceiling and the answer comes back as an empty string with
        # finish_reason=length, which parses as an LLM failure and silently
        # degrades to the deterministic fallback.
        max_tokens=RCA_MAX_TOKENS,
        # The token budget is charged after this returns, so it can never
        # stop a provider that simply never answers. This can.
        timeout=settings.llm_timeout_seconds,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        # Name the likely cause: on a reasoning model this almost always
        # means the token ceiling was consumed before `content` started.
        finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
        raise ValueError(
            f"LLM returned empty content (finish_reason={finish_reason}); "
            f"raise RCA_MAX_TOKENS if the model spends tokens on reasoning"
        )
    text = content.strip()
    if text.startswith("```"):  # tolerate fenced JSON despite instructions
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    parsed = LlmRcaResponse.model_validate(json.loads(text))
    if not parsed.hypotheses:
        raise ValueError("LLM returned no hypotheses")
    total_tokens = 0
    prompt_tokens = completion_tokens = 0
    try:
        usage = getattr(response, "usage", None)
        if usage is not None:
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    except (TypeError, ValueError):  # defensive: provider returned something odd
        total_tokens = prompt_tokens = completion_tokens = 0

    # Tokens say how much was consumed; only money says whether it was worth
    # it. Recorded here, at the one place that knows both the model and the
    # split, rather than reconstructed later from a total.
    from aiops_api import metrics
    from aiops_api.modules.rca.pricing import price_completion

    model_name = rca_model
    cost = price_completion(model_name, prompt_tokens, completion_tokens)
    metrics.investigation_cost_usd.labels(priced="yes" if cost.priced else "no").inc(cost.usd)
    logger.info(
        "llm completion cost",
        extra={
            "model": model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "usd": cost.usd,
            "priced": cost.priced,
        },
    )
    return parsed, total_tokens


def _resolve_llm_hypotheses(
    parsed: LlmRcaResponse, citations: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    """Map validated LLM refs to ids; drop unknown refs (never invent ids)."""
    hypotheses: list[dict[str, Any]] = []
    for item in parsed.hypotheses:
        evidence_refs, knowledge_refs = [], []
        for raw in item.evidence_refs:
            ref = _normalize_ref(raw)
            if ref and ref in citations["evidence"]:
                evidence_refs.append(ref)
        for raw in item.knowledge_refs:
            ref = _normalize_ref(raw)
            if ref and ref in citations["knowledge"]:
                knowledge_refs.append(ref)
        hypotheses.append(
            {
                "title": item.title,
                "confidence": item.confidence,
                "evidence_refs": evidence_refs,
                "knowledge_refs": knowledge_refs,
                "supporting_evidence": [citations["evidence"][ref] for ref in evidence_refs],
                "supporting_knowledge": [citations["knowledge"][ref] for ref in knowledge_refs],
            }
        )
    return hypotheses


def generate_rca(
    investigation: Any,
    evidence: list[Any],
    context_pack: dict | None,
    knowledge_results: list[Any],
    settings: Settings | None = None,
) -> tuple[str, list[dict], dict]:
    """Generate (draft_text, hypotheses, citations) for an investigation.

    hypotheses: list of dicts with title/confidence/evidence_refs/knowledge_refs
    plus supporting_evidence/supporting_knowledge id lists (persistence shape).
    citations: {"evidence": {E#: id}, "knowledge": {K#: id}}.
    """
    settings = settings or get_settings()
    citations = build_citations(evidence, knowledge_results)
    incident = _incident_view(investigation, context_pack)

    # The persisted agent config decides whether the LLM path runs at all;
    # env stays the fallback so an untouched deployment is unchanged.
    from aiops_api.modules.config import get_effective_config, model_for

    agent_config = get_effective_config(investigation.tenant_id)
    llm_model = model_for(agent_config, "rca", settings)

    with investigation_span(
        investigation.id,
        name="rca.generate",
        tenant_id=investigation.tenant_id,
        incident_id=investigation.incident_id,
        **{"llm.model": llm_model or "disabled"},
    ) as span:
        if llm_model:
            try:
                parsed, total_tokens = _call_llm(settings, incident, evidence, knowledge_results, citations)
                # Surface the LLM token cost to the orchestrator so the
                # per-investigation budget can charge it. Stored under a
                # key the service layer reads; stripped from the persisted
                # citation map (the budget tracker already counted it).
                citations["_llm_total_tokens"] = int(total_tokens or 0)
                hypotheses = _resolve_llm_hypotheses(parsed, citations)
                # The LLM cannot tell stub evidence from live — the prompt
                # shows it the same text either way. Apply the same
                # corroboration discount the deterministic path applies.
                hypotheses = annotate_hypotheses(hypotheses, evidence)
                span.set_attribute("llm.fallback", False)
                span.set_attribute("llm.total_tokens", int(total_tokens or 0))
                draft = render_draft(
                    incident=incident,
                    summary=parsed.summary,
                    hypotheses=hypotheses,
                    evidence=evidence,
                    knowledge=knowledge_results,
                    citations=citations,
                    investigation_id=investigation.id,
                )
                return draft, hypotheses, citations
            except Exception as exc:  # noqa: BLE001 — any LLM/parse failure degrades to deterministic
                span.set_attribute("llm.fallback", True)
                logger.warning(
                    "LLM RCA failed, using deterministic fallback",
                    extra={
                        "investigation_id": investigation.id,
                        "llm_model": llm_model,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        else:
            span.set_attribute("llm.fallback", True)

        result = deterministic_rca(incident, evidence, knowledge_results)
        return result["draft"], result["hypotheses"], result["citations"]

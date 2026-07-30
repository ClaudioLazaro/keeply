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

from aiops_api.modules.rca.draft import build_citations, item_field, item_id, render_draft
from aiops_api.modules.rca.fallback import deterministic_rca
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
    for item in evidence:
        marker = evidence_labels.get(item_id(item), "?")
        lines.append(f"[{marker}] {item_field(item, 'tool')}: {item_field(item, 'summary')}")
    lines.append("")
    lines.append("Knowledge documents:")
    for item in knowledge:
        marker = knowledge_labels.get(item_id(item), "?")
        chunk = str(item_field(item, "chunk"))[:500]
        lines.append(f"[{marker}] {item_field(item, 'title')}: {chunk}")
    return "\n".join(lines)


def _call_llm(settings: Settings, incident: dict, evidence: list[Any], knowledge: list[Any],
              citations: dict[str, dict[str, str]]) -> LlmRcaResponse:
    """One LiteLLM completion round-trip; raises on any failure."""
    import litellm  # lazy: the no-LLM path must not pay the import cost

    response = litellm.completion(
        model=settings.llm_model,
        api_key=settings.llm_api_key or None,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _llm_user_prompt(incident, evidence, knowledge, citations)},
        ],
        temperature=0,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM returned empty content")
    text = content.strip()
    if text.startswith("```"):  # tolerate fenced JSON despite instructions
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    parsed = LlmRcaResponse.model_validate(json.loads(text))
    if not parsed.hypotheses:
        raise ValueError("LLM returned no hypotheses")
    return parsed


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

    with investigation_span(
        investigation.id,
        name="rca.generate",
        tenant_id=investigation.tenant_id,
        incident_id=investigation.incident_id,
        **{"llm.model": settings.llm_model or "disabled"},
    ) as span:
        if settings.llm_model:
            try:
                parsed = _call_llm(settings, incident, evidence, knowledge_results, citations)
                hypotheses = _resolve_llm_hypotheses(parsed, citations)
                span.set_attribute("llm.fallback", False)
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
                        "llm_model": settings.llm_model,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        else:
            span.set_attribute("llm.fallback", True)

        result = deterministic_rca(incident, evidence, knowledge_results)
        return result["draft"], result["hypotheses"], result["citations"]

"""Deterministic, dependency-free RCA fallback.

Pure function: no LLM, no network, no DB. Used when AIOPS_LLM_MODEL is unset,
when the LLM path errors, and by the eval harness in-process.

Rule set (first match wins per evidence item, hypotheses ranked by rule order):
- CrashLoopBackOff / OOMKilled  -> container memory-limit hypothesis (0.7)
- 5xx / error-rate              -> application error-rate hypothesis (0.6)
- db / pool / connection        -> connection pool exhaustion hypothesis (0.5)
Always emits >= 1 hypothesis citing available evidence ids.
"""

import json
import re
from typing import Any

from aiops_api.modules.rca.draft import build_citations, item_field, item_id, render_draft
from aiops_api.modules.rca.provenance import annotate_hypotheses

_RULES: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"oomkilled|crashloopbackoff", re.IGNORECASE), "Container OOMKilled / memory limit", 0.7),
    (re.compile(r"\b5xx\b|error[ -]?rate", re.IGNORECASE), "Application error rate elevated", 0.6),
    (
        re.compile(r"connection pool|pool exhaustion|\bdb\b|\bpool\b", re.IGNORECASE),
        "Connection pool exhaustion",
        0.5,
    ),
)

_GENERIC_TITLE = "Root cause undetermined — requires manual investigation"
_GENERIC_CONFIDENCE = 0.3


def _evidence_text(item: Any) -> str:
    summary = item_field(item, "summary")
    payload = item_field(item, "payload", default=None)
    try:
        payload_text = json.dumps(payload) if payload is not None else ""
    except (TypeError, ValueError):
        payload_text = str(payload)
    return f"{summary}\n{payload_text}"


def _knowledge_text(item: Any) -> str:
    return f"{item_field(item, 'title')}\n{item_field(item, 'chunk')}"


def _match_hypotheses(evidence: list[Any], knowledge: list[Any]) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    for pattern, title, confidence in _RULES:
        matched_evidence = [item for item in evidence if pattern.search(_evidence_text(item))]
        if not matched_evidence:
            continue
        matched_knowledge = [item for item in knowledge if pattern.search(_knowledge_text(item))]
        hypotheses.append(
            {
                "title": title,
                "confidence": confidence,
                "evidence_refs": [],  # resolved to E# markers below
                "knowledge_refs": [],
                "supporting_evidence": [item_id(item) for item in matched_evidence],
                "supporting_knowledge": [item_id(item) for item in matched_knowledge],
            }
        )
    if not hypotheses:
        hypotheses.append(
            {
                "title": _GENERIC_TITLE,
                "confidence": _GENERIC_CONFIDENCE,
                "evidence_refs": [],
                "knowledge_refs": [],
                "supporting_evidence": [item_id(item) for item in evidence],
                "supporting_knowledge": [],
            }
        )
    return hypotheses


def _resolve_refs(
    hypotheses: list[dict[str, Any]], citations: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    """Fill evidence_refs/knowledge_refs with the E#/K# markers for the ids."""
    evidence_markers = {v: k for k, v in citations["evidence"].items()}
    knowledge_markers = {v: k for k, v in citations["knowledge"].items()}
    for hypothesis in hypotheses:
        hypothesis["evidence_refs"] = [
            evidence_markers[eid] for eid in hypothesis["supporting_evidence"] if eid in evidence_markers
        ]
        hypothesis["knowledge_refs"] = [
            knowledge_markers[kid] for kid in hypothesis["supporting_knowledge"] if kid in knowledge_markers
        ]
    return hypotheses


def deterministic_rca(incident: dict, evidence: list, knowledge: list) -> dict:
    """Pure deterministic RCA: hypotheses + cited markdown draft + citations map.

    Returns {"hypotheses": [...], "draft": str, "citations": {...}}.
    """
    citations = build_citations(evidence, knowledge)
    hypotheses = _resolve_refs(_match_hypotheses(evidence, knowledge), citations)
    # Discount anything no live evidence backs before it reaches the draft.
    hypotheses = annotate_hypotheses(hypotheses, evidence)
    draft = render_draft(
        incident=incident,
        summary=None,
        hypotheses=hypotheses,
        evidence=evidence,
        knowledge=knowledge,
        citations=citations,
        investigation_id=str(incident.get("investigation_id") or ""),
    )
    return {"hypotheses": hypotheses, "draft": draft, "citations": citations}

"""Citation map + markdown draft rendering shared by the fallback and LLM paths.

Citation contract (investigation.rca_citations):
    {"evidence": {"E1": evidence_id, ...}, "knowledge": {"K1": knowledge_doc_id, ...}}

The draft text cites [E#]/[K#] markers; every marker MUST resolve through the
citations map, so markers are only ever rendered from this module.
"""

from typing import Any

DISCLAIMER_LINE = "_Mode: suggest-only — no actions taken._"


def item_id(item: Any) -> str:
    """Accept Evidence rows, dicts, or attribute objects."""
    if isinstance(item, dict):
        return str(item.get("id", ""))
    return str(getattr(item, "id", ""))


def item_field(item: Any, field: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def build_citations(evidence: list[Any], knowledge: list[Any]) -> dict[str, dict[str, str]]:
    """Assign E1..En / K1..Km labels in input order."""
    return {
        "evidence": {f"E{i + 1}": item_id(item) for i, item in enumerate(evidence)},
        "knowledge": {f"K{i + 1}": item_id(item) for i, item in enumerate(knowledge)},
    }


def default_summary(incident: dict[str, Any], evidence: list[Any], knowledge: list[Any]) -> str:
    incident_id = incident.get("id") or incident.get("incident_id") or "unknown"
    tenant_id = incident.get("tenant_id") or "unknown"
    name = incident.get("name") or incident.get("user_generated_name") or ""
    name_part = f" — {name}" if name else ""
    return (
        f"Incident `{incident_id}` (tenant `{tenant_id}`){name_part}. "
        f"{len(evidence)} evidence items gathered (read-only); "
        f"{len(knowledge)} knowledge references consulted."
    )


def _provenance_marker(item: Any) -> str:
    """Inline provenance tag for an evidence bullet.

    Live evidence is unmarked (it is the expected case); anything else is
    called out so a reader scanning the list can see what is demo data.
    """
    from aiops_api.modules.rca.provenance import GAP, LIVE, STUB, evidence_backend

    backend = evidence_backend(item)
    if backend == LIVE:
        return ""
    if backend == STUB:
        return " _(stub — demo data)_"
    if backend == GAP:
        return ""  # the summary already says "evidence gap"
    return " _(provenance unknown)_"


def _format_refs(label: str, refs: list[str]) -> str:
    if not refs:
        return f"{label}: none"
    return f"{label}: " + ", ".join(f"[{ref}]" for ref in refs)


def render_draft(
    *,
    incident: dict[str, Any],
    summary: str | None,
    hypotheses: list[dict[str, Any]],
    evidence: list[Any],
    knowledge: list[Any],
    citations: dict[str, dict[str, str]],
    investigation_id: str,
) -> str:
    """Render the RCA draft markdown.

    Sections: Summary / Hypotheses (confidence + citations) / Evidence /
    Knowledge references / suggest-only disclaimer. Hypothesis prose is
    noun-phrase only — never imperative remediation verbs.
    """
    from aiops_api.modules.rca.provenance import describe

    lines = [
        "**RCA draft (AI-assisted)**",
        "",
        "## Summary",
        summary or default_summary(incident, evidence, knowledge),
        "",
        describe(evidence),
        "",
        "## Hypotheses",
    ]
    for index, hypothesis in enumerate(hypotheses, start=1):
        refs = ", ".join(
            (
                _format_refs("evidence", list(hypothesis.get("evidence_refs") or [])),
                _format_refs("knowledge", list(hypothesis.get("knowledge_refs") or [])),
            )
        )
        caveat = hypothesis.get("caveat")
        caveat_part = f" ⚠️ _{caveat}_" if caveat else ""
        lines.append(
            f"{index}. **{hypothesis['title']}** "
            f"(confidence: {hypothesis['confidence']:.2f}){caveat_part} — {refs}"
        )
    if not hypotheses:  # defensive: engine guarantees >= 1
        lines.append("_No hypotheses generated._")

    lines += ["", "## Evidence"]
    evidence_labels = {v: k for k, v in citations.get("evidence", {}).items()}
    for item in evidence:
        marker = evidence_labels.get(item_id(item), "?")
        lines.append(
            f"- [{marker}] {item_field(item, 'tool')}: "
            f"{item_field(item, 'summary')}{_provenance_marker(item)}"
        )
    if not evidence:
        lines.append("_No evidence gathered._")

    lines += ["", "## Knowledge references"]
    knowledge_labels = {v: k for k, v in citations.get("knowledge", {}).items()}
    for item in knowledge:
        marker = knowledge_labels.get(item_id(item), "?")
        source = item_field(item, "source")
        source_part = f" ({source})" if source else ""
        lines.append(f"- [{marker}] {item_field(item, 'title')}{source_part}")
    if not knowledge:
        lines.append("_No knowledge documents matched._")

    lines += [
        "",
        "---",
        DISCLAIMER_LINE,
        f"_Investigation id: {investigation_id}_",
    ]
    return "\n".join(lines)

"""Evidence provenance: how much of an RCA rests on real data.

A stub tool result is a canned demo payload. Once it is a dict it is
indistinguishable from production telemetry, so without this module an RCA
built entirely on demo data renders exactly like one built on live
telemetry — same confident wording, same numbered citations. That is worse
than having no RCA at all, because it invites an operator to act on it
during an incident.

Two things are derived here and surfaced everywhere the draft goes:

1. a per-investigation tally (live / stub / gap), rendered into the summary
2. a per-hypothesis corroboration check — a hypothesis supported only by
   stub evidence gets its confidence discounted and is labelled unverified
"""

from typing import Any

from aiops_api.modules.rca.draft import item_field, item_id

LIVE = "live"
STUB = "stub"
GAP = "gap"

# A hypothesis with no live evidence behind it keeps its ordering but loses
# most of its confidence: the pattern matched, but only against demo data.
UNCORROBORATED_CONFIDENCE_FACTOR = 0.4
UNCORROBORATED_LABEL = "unverified — stub data only"


def evidence_backend(item: Any) -> str:
    """Provenance of one evidence item, defaulting to 'unknown'.

    Reads the persisted column first; falls back to the payload for rows
    written before the column existed.
    """
    backend = item_field(item, "backend", default="")
    if isinstance(backend, str) and backend:
        return backend
    payload = item_field(item, "payload", default=None)
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            reported = result.get("backend")
            if isinstance(reported, str) and reported:
                return reported
        if payload.get("error"):
            return GAP
    return "unknown"


def tally(evidence: list[Any]) -> dict[str, int]:
    """Count evidence items per provenance bucket."""
    counts: dict[str, int] = {}
    for item in evidence:
        counts[evidence_backend(item)] = counts.get(evidence_backend(item), 0) + 1
    return counts


def describe(evidence: list[Any]) -> str:
    """One-line provenance sentence for the draft summary.

    Always states the split explicitly — never silently implies the
    evidence is real.
    """
    counts = tally(evidence)
    live, stub, gap = counts.get(LIVE, 0), counts.get(STUB, 0), counts.get(GAP, 0)
    unknown = sum(v for k, v in counts.items() if k not in (LIVE, STUB, GAP))
    parts = [f"{live} live"]
    if stub:
        parts.append(f"{stub} stub (demo data)")
    if gap:
        parts.append(f"{gap} gap")
    if unknown:
        parts.append(f"{unknown} unknown provenance")
    sentence = "Evidence provenance: " + ", ".join(parts) + "."
    if stub and live == 0:
        sentence += (
            " **No live evidence was collected — this analysis rests entirely on"
            " demo data and must not be used to make incident decisions.**"
        )
    elif stub:
        sentence += " Hypotheses supported only by stub evidence are marked unverified."
    return sentence


def annotate_hypotheses(
    hypotheses: list[dict[str, Any]], evidence: list[Any]
) -> list[dict[str, Any]]:
    """Discount and label hypotheses that no live evidence supports.

    ``corroborated`` is added to every hypothesis so downstream consumers
    (UI, eval harness) do not have to re-derive it. Confidence is only ever
    reduced — never inflated.
    """
    live_ids = {item_id(item) for item in evidence if evidence_backend(item) == LIVE}

    for hypothesis in hypotheses:
        supporting = list(hypothesis.get("supporting_evidence") or [])
        corroborated = any(eid in live_ids for eid in supporting)
        hypothesis["corroborated"] = corroborated
        if not corroborated:
            hypothesis["confidence"] = round(
                float(hypothesis.get("confidence", 0.0)) * UNCORROBORATED_CONFIDENCE_FACTOR, 2
            )
            hypothesis["caveat"] = UNCORROBORATED_LABEL
    return hypotheses

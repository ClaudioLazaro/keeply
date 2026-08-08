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
# The caveat names where the support actually came from. A single label
# claimed "stub data only" even for a hypothesis whose every supporting
# call had failed, which is a false statement about provenance in the one
# place an operator looks to judge it.
NO_EVIDENCE_LABEL = "unverified — no evidence cited"
FAILED_EVIDENCE_LABEL = "unverified — every supporting call failed"
NO_LIVE_LABEL = "unverified — no live evidence"


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


_DO_NOT_DECIDE = " and this must not be used to make incident decisions."


def _zero_live_reason(counts: dict[str, int], stub: int, gap: int, unknown: int) -> str:
    """Why an analysis with no live evidence is untrustworthy, accurately.

    Every branch here says something specific about where the evidence came
    from, so none of them may be the default. Falling through to the demo
    -data wording described tool results of unknown provenance as demo
    data, which is a different — and unfounded — claim.
    """
    if not counts:
        return (
            "No evidence was collected at all — nothing here was checked "
            "against your systems," + _DO_NOT_DECIDE
        )
    if gap and not stub and not unknown:
        return "Every evidence-gathering call failed — nothing was verified," + _DO_NOT_DECIDE
    if stub and not unknown:
        return (
            "No live evidence was collected — this analysis rests entirely on "
            "demo data" + _DO_NOT_DECIDE
        )
    if unknown and not stub:
        return (
            "No live evidence was collected — the tools that answered did not "
            "say whether their data was real," + _DO_NOT_DECIDE
        )
    return (
        "No live evidence was collected — nothing here was confirmed against "
        "your systems," + _DO_NOT_DECIDE
    )


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

    # Warn on every zero-live case, not just the stub one. An investigation
    # that collected nothing, or whose every call failed, is weaker than one
    # built on demo data — yet those two said nothing at all while a single
    # stub item produced a bold warning.
    if live == 0:
        sentence += f" **{_zero_live_reason(counts, stub, gap, unknown)}**"
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
    backends = {item_id(item): evidence_backend(item) for item in evidence}
    live_ids = {eid for eid, backend in backends.items() if backend == LIVE}

    for hypothesis in hypotheses:
        supporting = list(hypothesis.get("supporting_evidence") or [])
        live = sum(1 for eid in supporting if eid in live_ids)
        share = live / len(supporting) if supporting else 0.0

        hypothesis["corroborated"] = live > 0
        hypothesis["live_support"] = live
        hypothesis["support_count"] = len(supporting)
        hypothesis["corroboration"] = round(share, 2)

        # Scaled by how much of the support is real, not by whether ANY of it
        # is. The previous rule was `any(live)`, so one live item among four
        # conferred full confidence — a hypothesis resting 25% on reality was
        # presented exactly like one resting entirely on it. Technically true,
        # materially misleading, and in the one number an operator reads to
        # decide whether to act.
        #
        # Monotonic and bounded: share=0 keeps the old 0.4 floor, share=1
        # leaves confidence untouched. Confidence is only ever reduced.
        factor = UNCORROBORATED_CONFIDENCE_FACTOR + (
            1 - UNCORROBORATED_CONFIDENCE_FACTOR
        ) * share
        if factor < 1.0:
            hypothesis["confidence"] = round(
                float(hypothesis.get("confidence", 0.0)) * factor, 2
            )
        if live == 0:
            hypothesis["caveat"] = _caveat_for(supporting, backends)
        elif share < 1.0:
            hypothesis["caveat"] = (
                f"partly unverified — {live} of {len(supporting)} supporting "
                "items are live"
            )
    return hypotheses


# An analysis with no live evidence anywhere cannot separate one cause from
# another: it is ranking stories, not findings.
def is_conclusive(evidence: list[Any]) -> bool:
    """Whether the evidence base can support ranking causes at all.

    The system previously always answered. Given sixteen demo payloads and
    two real ones it still produced five ranked hypotheses with confidence
    scores, and a ranked list reads as an assessment however it is labelled —
    a number anchors an operator even when the caveat beside it does not.

    Declining is itself a finding, and a more useful one than a confident
    guess during an incident.
    """
    return tally(evidence).get(LIVE, 0) > 0


INCONCLUSIVE_NOTICE = (
    "**Inconclusive: no live evidence was collected.** The hypotheses below "
    "are patterns matched against demo or missing data, not findings about "
    "your systems. Treat them as questions to investigate, never as an "
    "assessment."
)


def _caveat_for(supporting: list[str], backends: dict[str, str]) -> str:
    """Name why a hypothesis is unverified, accurately.

    The label appears next to a discounted confidence score and is the one
    place an operator reads to judge what the number is worth, so it must
    describe the evidence that actually exists.
    """
    if not supporting:
        return NO_EVIDENCE_LABEL
    kinds = {backends.get(eid, "unknown") for eid in supporting}
    if kinds == {STUB}:
        return UNCORROBORATED_LABEL
    if kinds == {GAP}:
        return FAILED_EVIDENCE_LABEL
    return NO_LIVE_LABEL

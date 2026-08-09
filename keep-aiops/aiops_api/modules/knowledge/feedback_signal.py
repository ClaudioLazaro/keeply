"""Let the operator's verdict change what gets retrieved next time.

``investigation_feedback`` filled up, the UI collected thumbs, and nothing
read the table. A product that asks for a rating and never uses it teaches the
operator to stop rating — the signal decays before anything can learn from it.

What the signal is good for is narrow and worth stating. A thumbs-down does
not mean the retrieved runbook was wrong; it means the resulting analysis was
not useful, which could be the retrieval, the evidence, or the writer. So this
is a **weak prior on ranking**, never a filter: a document is nudged, never
excluded, and a single bad rating cannot bury something.

Bounded on both sides for the same reason. Left unbounded, one heavily-rated
document would dominate every retrieval and the corpus would collapse to
whatever was popular during the first month of use.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlmodel import select

from aiops_api.db import session_scope

logger = logging.getLogger(__name__)

# A document seen in useful investigations is ranked at most 15% higher; one
# seen only in unhelpful ones at most 15% lower. Enough to break ties, far too
# little to override a genuine keyword or embedding match.
MAX_ADJUSTMENT = 0.15

# Below this many ratings the sample is noise, and acting on it would just add
# jitter to an otherwise deterministic ranking.
MIN_RATINGS = 2


def _document_ratings(tenant_id: str) -> dict[str, tuple[int, int]]:
    """Per knowledge document: (useful, not_useful) counts.

    Joins feedback to the documents each investigation actually cited, so a
    rating only touches what the analysis was built on.
    """
    from aiops_api.modules.feedback.models import InvestigationFeedback
    from aiops_api.modules.orchestrator.models import Investigation

    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with session_scope() as session:
        rows = session.exec(
            select(InvestigationFeedback, Investigation)
            .join(Investigation, Investigation.id == InvestigationFeedback.investigation_id)
            .where(InvestigationFeedback.tenant_id == tenant_id)
        ).all()
        for feedback, investigation in rows:
            citations = (investigation.rca_citations or {}).get("knowledge") or {}
            for doc_id in citations.values():
                if not isinstance(doc_id, str):
                    continue
                index = 0 if feedback.rating == "useful" else 1
                tally[doc_id][index] += 1
    return {doc: (counts[0], counts[1]) for doc, counts in tally.items()}


def adjustment_for(useful: int, not_useful: int) -> float:
    """Multiplier for a document's retrieval score. Never raises.

    Returns 1.0 — no opinion — below the sample floor, so a corpus with little
    feedback ranks exactly as it did before this existed.
    """
    total = useful + not_useful
    if total < MIN_RATINGS:
        return 1.0
    share = useful / total  # 0.0 .. 1.0
    return 1.0 + MAX_ADJUSTMENT * (2 * share - 1)


def apply(tenant_id: str, results: list[dict]) -> list[dict]:
    """Re-rank retrieved documents by how the analyses citing them landed.

    Best-effort by construction: retrieval must keep working when the feedback
    tables are missing, empty or unreachable. A failure here downgrades the
    ranking to what it was, never the investigation to a gap.
    """
    if not results:
        return results
    try:
        ratings = _document_ratings(tenant_id)
    except Exception:  # noqa: BLE001 — ranking is an optimisation, not a dependency
        logger.info("feedback signal unavailable; ranking unchanged", exc_info=True)
        return results
    if not ratings:
        return results

    adjusted = 0
    for item in results:
        doc_id = item.get("id")
        if not isinstance(doc_id, str) or doc_id not in ratings:
            continue
        useful, not_useful = ratings[doc_id]
        factor = adjustment_for(useful, not_useful)
        if factor == 1.0:
            continue
        item["score"] = round(float(item.get("score", 0.0)) * factor, 6)
        # Recorded on the row so a surprising ranking can be explained rather
        # than guessed at — the same reason find_workload reports matched_by.
        item["feedback_factor"] = round(factor, 3)
        item["feedback_ratings"] = {"useful": useful, "not_useful": not_useful}
        adjusted += 1

    if adjusted:
        logger.info("applied feedback signal to retrieval", extra={"documents": adjusted})
    # Same deterministic ordering the retriever uses, so equal scores stay stable.
    return sorted(results, key=lambda r: (-float(r.get("score", 0.0)), str(r.get("title", "")), str(r.get("id", ""))))

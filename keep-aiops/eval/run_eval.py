"""Golden-set eval harness for MVP AC4: ≥60% useful RCA drafts.

Runs the deterministic investigation path fully IN-PROCESS against the golden
set — no LLM, no network, no DB. It imports the M2 sibling contracts directly:

- ``aiops_api.modules.orchestrator.models.Evidence`` — evidence shape
- ``aiops_api.modules.knowledge.keyword_retrieve`` — deterministic keyword retrieval
- ``aiops_api.modules.rca.fallback.deterministic_rca`` — deterministic RCA fallback
  producing a draft with ``[E#]`` / ``[K#]`` citation markers

Each fixture is scored against a transparent rubric (1 point each, useful = ≥3/4):

1. ≥ ``expected_min_citations`` citation markers (``[E#]``/``[K#]``) in the draft
2. ≥ 1 evidence marker (``[E#]``) that resolves in the citations map
3. ≥ 1 expected keyword present (case-insensitive)
4. suggest-only disclaimer present AND no mutate/imperative remediation claims
   (``restart`` / ``kubectl delete`` / ``scale`` outside negation) in generated
   prose. Verbatim-quoted sections (Evidence / Knowledge references / quotes /
   code blocks) are excluded from the mutate scan — stub evidence legitimately
   contains strings like "Back-off restarting failed container".

Entrypoint: ``python -m eval.run_eval`` (from the keep-aiops directory).
Exit code 1 when the useful-ratio drops below 0.6 (AC4).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

GOLDEN_SET_DIR = Path(__file__).resolve().parent / "golden_set"
USEFUL_RATIO_THRESHOLD = 0.6
USEFUL_MIN_SCORE = 3  # rubric points out of 4

# --------------------------------------------------------------------------- #
# Sibling M2 contracts (defensive imports — see batch contract)
# --------------------------------------------------------------------------- #

Contracts = tuple[type, Callable[..., list[dict]], Callable[..., dict]]


def load_contracts() -> Contracts:
    """Import the sibling M2 contracts this harness scores against.

    Raises an explicit ImportError naming exactly what is missing, so a run
    before a sibling slice lands fails loudly instead of mysteriously.
    """
    try:
        from aiops_api.modules.orchestrator.models import Evidence
    except ImportError as exc:  # pragma: no cover - landed in M1, should never trip
        raise ImportError(
            "eval harness requires aiops_api.modules.orchestrator.models.Evidence "
            "(orchestrator slice) — is keep-aiops installed (`pip install -e .`)?"
        ) from exc

    try:
        from aiops_api.modules.knowledge import keyword_retrieve
    except ImportError as exc:
        raise ImportError(
            "eval harness requires aiops_api.modules.knowledge.keyword_retrieve "
            "(Knowledge slice, M2 contract) — the knowledge module has not landed "
            "yet or its import failed"
        ) from exc

    try:
        from aiops_api.modules.rca.fallback import deterministic_rca
    except ImportError as exc:
        raise ImportError(
            "eval harness requires aiops_api.modules.rca.fallback.deterministic_rca "
            "(RCA slice, M2 contract) — the rca module has not landed yet or its "
            "import failed"
        ) from exc

    return Evidence, keyword_retrieve, deterministic_rca


# --------------------------------------------------------------------------- #
# Rubric primitives
# --------------------------------------------------------------------------- #

CITATION_MARKER_RE = re.compile(r"\[(?:E|K)\d+\]")
EVIDENCE_MARKER_RE = re.compile(r"\[E\d+\]")
DISCLAIMER_RE = re.compile(
    r"suggest[-\s]?only"
    r"|no\s+(?:automated|automatic|autonomous)\s+(?:actions?|changes?|remediation)"
    r"|human\s+(?:review|approval|operator|confirmation|intervention)",
    re.IGNORECASE,
)

# Mutate/imperative remediation verbs guarded by the suggest-only policy.
# Word boundaries keep "ScalingReplicaSet" (event reason) from matching "scale".
MUTATE_TERM_RE = re.compile(
    r"\brestart(?:s|ed|ing)?\b|\bkubectl\s+delete\b|\bscal(?:e|ed|ing)\b",
    re.IGNORECASE,
)
# A mutate term is acceptable only when clearly negated in its immediate context
# ("do not restart", "without scaling", "no restart required", ...).
NEGATION_RE = re.compile(
    r"(?:do\s+not|don'?t|never|no|not|without|avoid(?:ing)?|refrain\s+from"
    r"|rather\s+than|instead\s+of)\W+(?:\w+\W+){0,3}$",
    re.IGNORECASE,
)
_NEGATION_WINDOW = 60

# Sections whose content is verbatim-quoted evidence/knowledge, not generated
# prose — excluded from the mutate-claim scan.
_QUOTED_SECTION_TITLE_RE = re.compile(
    r"evidence|knowledge|reference|citation|sources?|appendix|quote",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")


def extract_generated_prose(draft: str) -> str:
    """Strip verbatim-quoted content, leaving only generated prose.

    Removes fenced code blocks, blockquotes, and every markdown section whose
    header names it as quoted material (Evidence / Knowledge references / ...).
    The suggest-only mutate guard must judge the draft's own claims, not the
    raw tool output it quotes (stub strings like "Back-off restarting failed
    container" are observations, not remediation commands).
    """
    text = _FENCED_CODE_RE.sub(" ", draft)
    kept: list[str] = []
    dropping = False
    for line in text.splitlines():
        header = _HEADER_RE.match(line)
        if header:
            dropping = bool(_QUOTED_SECTION_TITLE_RE.search(header.group(1)))
            continue
        if dropping or line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept)


def find_mutate_claims(prose: str) -> list[str]:
    """Return mutate/imperative terms used outside a negated context."""
    claims: list[str] = []
    for match in MUTATE_TERM_RE.finditer(prose):
        window = prose[max(0, match.start() - _NEGATION_WINDOW) : match.start()]
        if NEGATION_RE.search(window):
            continue
        claims.append(match.group(0))
    return claims


@dataclass
class RubricScore:
    """One boolean per rubric point plus the evidence behind each verdict."""

    has_min_citations: bool
    has_evidence_ref: bool
    has_keyword: bool
    has_disclaimer_no_mutate: bool
    citation_count: int
    matched_keywords: list[str] = field(default_factory=list)
    disclaimer_found: bool = False
    mutate_claims: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return int(self.has_min_citations) + int(self.has_evidence_ref) + int(self.has_keyword) + int(
            self.has_disclaimer_no_mutate
        )

    @property
    def useful(self) -> bool:
        return self.total >= USEFUL_MIN_SCORE


def score_draft(fixture: dict[str, Any], draft: str, citations: dict[str, Any] | None = None) -> RubricScore:
    """Score one RCA draft against the transparent AC4 rubric."""
    markers = CITATION_MARKER_RE.findall(draft)
    min_citations = int(fixture.get("expected_min_citations", 2))

    evidence_markers = EVIDENCE_MARKER_RE.findall(draft)
    has_evidence_ref = bool(evidence_markers)
    if has_evidence_ref and citations:
        known = set((citations.get("evidence") or {}).keys())
        if known:
            # Stricter: at least one marker must resolve to a real evidence id.
            has_evidence_ref = any(marker.strip("[]") in known for marker in evidence_markers)

    lowered = draft.lower()
    matched_keywords = [kw for kw in fixture.get("expected_keywords", []) if kw.lower() in lowered]

    disclaimer_found = bool(DISCLAIMER_RE.search(draft))
    mutate_claims = find_mutate_claims(extract_generated_prose(draft))

    return RubricScore(
        has_min_citations=len(markers) >= min_citations,
        has_evidence_ref=has_evidence_ref,
        has_keyword=bool(matched_keywords),
        has_disclaimer_no_mutate=disclaimer_found and not mutate_claims,
        citation_count=len(markers),
        matched_keywords=matched_keywords,
        disclaimer_found=disclaimer_found,
        mutate_claims=mutate_claims,
    )


# --------------------------------------------------------------------------- #
# Deterministic pipeline (mirrors the M2 FSM: gather -> retrieve -> draft)
# --------------------------------------------------------------------------- #


def summarize_evidence(tool: str, payload: dict[str, Any]) -> str:
    """Signal-rich one-line summary for an evidence record.

    The draft's Evidence section quotes summaries verbatim, so the summary
    surfaces the payload's key signals (reason names, messages, first log
    lines) instead of M1's terse "N items returned".
    """
    if tool == "get_pods":
        pods = payload.get("pods") or []
        bits = []
        for pod in pods:
            state = pod.get("state") or {}
            waiting = (state.get("waiting") or {}).get("reason")
            running = "Running" if state.get("running") else None
            terminated = (state.get("terminated") or {}).get("reason")
            last = pod.get("last_terminated") or {}
            bits.append(
                f"{pod.get('name')} phase={pod.get('phase')} ready={pod.get('ready')} "
                f"restarts={pod.get('restarts')} state={waiting or terminated or running or 'unknown'} "
                f"last_terminated={last.get('reason')}({last.get('exit_code')})"
            )
        return f"get_pods: {'; '.join(bits)}" if bits else "get_pods: no pods"
    if tool == "get_events":
        events = payload.get("events") or []
        bits = [f"{e.get('reason')}: {e.get('message')}" for e in events]
        return f"get_events: {'; '.join(bits)}" if bits else "get_events: no events"
    if tool == "get_logs":
        lines = payload.get("lines") or []
        if isinstance(payload.get("logs"), str):
            lines = payload["logs"].splitlines()
        head = " | ".join(str(line) for line in lines[:6])
        return f"get_logs ({payload.get('pod', '?')}): {head}" if head else "get_logs: no lines"
    if tool == "prom_alerts":
        alerts = payload.get("alerts") or []
        bits = [f"{a.get('name')}({a.get('severity')}): {a.get('summary')}" for a in alerts]
        return f"prom_alerts: {'; '.join(bits)}" if bits else "prom_alerts: none firing"
    if tool in ("prom_query", "prom_query_range"):
        if "series" in payload:
            series = ", ".join(f"{s.get('pod') or s.get('metric', '?')}={s.get('value')}" for s in payload["series"])
            return f"prom_query: {payload.get('query')} => [{series}] {payload.get('unit', '')}"
        return f"prom_query: {payload.get('query')} => {payload.get('value')} {payload.get('unit', '')}"
    return f"{tool}: {json.dumps(payload, sort_keys=True)[:300]}"


@dataclass
class FixtureResult:
    fixture: dict[str, Any]
    draft: str
    rubric: RubricScore
    retrieved_ids: list[str]


def run_fixture(fixture: dict[str, Any], contracts: Contracts) -> FixtureResult:
    """Run the deterministic pipeline for one fixture and score the draft."""
    Evidence, keyword_retrieve, deterministic_rca = contracts

    evidence = [
        Evidence(
            id=f"ev-{fixture['id']}-{index + 1}",
            investigation_id=f"eval-{fixture['id']}",
            tool=item["tool"],
            summary=summarize_evidence(item["tool"], item["payload"]),
            payload=item["payload"],
        )
        for index, item in enumerate(fixture["stub_evidence"])
    ]
    docs = [
        {
            "id": f"doc-{fixture['id']}-{index + 1}",
            "title": doc["title"],
            "source": "golden-set",
            "chunk": doc["chunk"],
        }
        for index, doc in enumerate(fixture["stub_knowledge"])
    ]
    incident = fixture["incident"]
    query = f"{incident.get('name', '')} {incident.get('service', '')} {fixture.get('description', '')}"
    retrieved = keyword_retrieve(query, docs, k=min(5, len(docs)))

    result = deterministic_rca(incident, evidence, retrieved)
    draft = result["draft"]
    citations = result.get("citations")

    return FixtureResult(
        fixture=fixture,
        draft=draft,
        rubric=score_draft(fixture, draft, citations),
        retrieved_ids=[doc.get("id", "?") for doc in retrieved],
    )


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def load_fixtures(golden_dir: Path) -> list[dict[str, Any]]:
    paths = sorted(golden_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no golden-set fixtures found in {golden_dir}")
    return [json.loads(path.read_text()) for path in paths]


def _ok(flag: bool) -> str:
    return "ok" if flag else "FAIL"


def print_report(results: list[FixtureResult], verbose: bool = False) -> float:
    print("AC4 golden-set evaluation — deterministic path (keyword retrieval + deterministic RCA, no LLM/network)\n")
    header = f"{'fixture':<22} {'score':>5} {'useful':>6}  {'citations':<18} {'evidence':<9} {'keyword':<24} disclaimer/no-mutate"
    print(header)
    print("-" * len(header))
    useful_count = 0
    for result in results:
        rubric = result.rubric
        useful_count += int(rubric.useful)
        citations = f"{_ok(rubric.has_min_citations)} ({rubric.citation_count} markers)"
        keywords = f"{_ok(rubric.has_keyword)} ({', '.join(rubric.matched_keywords) or 'none'})"
        if rubric.has_disclaimer_no_mutate:
            clean = "ok"
        elif not rubric.disclaimer_found:
            clean = "FAIL (no disclaimer)"
        else:
            clean = f"FAIL (mutate: {', '.join(rubric.mutate_claims)})"
        print(
            f"{result.fixture['id']:<22} {rubric.total}/4  {'YES' if rubric.useful else 'no':>6}  "
            f"{citations:<18} {_ok(rubric.has_evidence_ref):<9} {keywords:<24} {clean}"
        )
    ratio = useful_count / len(results)
    verdict = "PASS" if ratio >= USEFUL_RATIO_THRESHOLD else "FAIL"
    print(
        f"\nuseful-ratio: {useful_count}/{len(results)} = {ratio:.2f} "
        f"(threshold {USEFUL_RATIO_THRESHOLD:.2f}) — {verdict}"
    )
    if verbose:
        for result in results:
            print(f"\n{'=' * 78}\n# {result.fixture['id']} — retrieved knowledge: {result.retrieved_ids}\n{'=' * 78}")
            print(result.draft)
    return ratio


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AC4 golden-set eval harness (deterministic, in-process)")
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_SET_DIR, help="directory of fixture JSON files")
    parser.add_argument("--verbose", action="store_true", help="print full drafts after the report")
    args = parser.parse_args(argv)

    contracts = load_contracts()
    fixtures = load_fixtures(args.golden_dir)
    results = [run_fixture(fixture, contracts) for fixture in fixtures]
    ratio = print_report(results, verbose=args.verbose)
    return 0 if ratio >= USEFUL_RATIO_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())

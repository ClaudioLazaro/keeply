"""Plain-language wording for rule proposals.

The model writes; it does not decide. Which alerts belong together, how
often the pattern recurred and whether it clears the threshold are all
settled by deterministic scoring before this module is called — an
operator who disagrees with a grouping can read which signal caused it
and change that setting. Handing that judgement to an LLM would replace a
number they can tune with an opinion they can only disagree with.

What a model is genuinely better at is saying what the numbers mean. So
it gets exactly two jobs: name the pattern, and explain it in one
sentence. The counts stay computed and are never model-written, because a
hallucinated "seen 5 times" would look identical to a real one.

Model and credential come from the same agent config as the RCA path
(Settings -> AI Agents), so there is one place that decides which LLM this
product uses.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiops_api.modules.correlation.rules import RuleProposal

logger = logging.getLogger(__name__)

# Short: the name becomes a Keep rule name, shown in a table column.
MAX_NAME_LENGTH = 60
MAX_SUMMARY_LENGTH = 300

# The name is passed to Keep as `ruleName`. Control characters and quotes
# have no business there, and refusing them is cheaper than reasoning about
# where the string ends up.
_NAME_SAFE = re.compile(r"^[A-Za-z0-9 ._\-/()]+$")

SYSTEM_PROMPT = """You name and explain alert-correlation patterns for an SRE tool.

A deterministic scorer has ALREADY decided that these alerts belong \
together and that the pattern recurs often enough to be worth a rule. That \
decision is not yours to make or question.

Your job is only to word it:
- "name": a short human label for the pattern (max 60 chars). Describe the \
failure, not the mechanism. Prefer "Checkout API error spike" over \
"service==checkout-api correlation".
- "summary": ONE sentence telling an on-call engineer what this rule would \
group and why that is useful.

Rules:
- Never state counts, frequencies, or time spans. Those are computed and \
shown separately; inventing them is worse than omitting them.
- Never claim a root cause. You are naming a pattern, not diagnosing it.
- Use only the service, source and alert names given to you.

Reply with JSON only: {"name": "...", "summary": "..."}"""


def _user_prompt(proposal: "RuleProposal", samples: list[str]) -> str:
    lines = [
        f"CEL matched by the rule: {proposal.cel}",
        f"Grouped by: {', '.join(proposal.grouping_criteria) or 'nothing'}",
    ]
    if samples:
        lines.append("Alert names seen in this pattern:")
        lines.extend(f"- {name}" for name in samples[:10])
    return "\n".join(lines)


def _clean(value: Any, limit: int) -> str:
    """Collapse a model string to one tidy line, or return empty."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:limit].strip()


def _apply_one(proposal: "RuleProposal", samples: list[str], model: str, api_key: str | None) -> None:
    import litellm  # lazy: the no-LLM path must not pay the import cost

    response = litellm.completion(
        model=model,
        api_key=api_key or None,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(proposal, samples)},
        ],
        temperature=0,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty completion")

    parsed = json.loads(content)
    name = _clean(parsed.get("name"), MAX_NAME_LENGTH)
    summary = _clean(parsed.get("summary"), MAX_SUMMARY_LENGTH)

    # Only take the name if it is safe to hand to Keep verbatim.
    if name and _NAME_SAFE.match(name):
        proposal.name = name
    if summary:
        # Computed evidence first, prose second: what the operator can
        # verify leads, and it stays obvious which half is which.
        proposal.rationale = f"{proposal.rationale} {summary}"


def apply_wording(proposals: list["RuleProposal"], tenant_id: str, samples_by_cel: dict[str, list[str]] | None = None) -> list["RuleProposal"]:
    """Rewrite proposal names and add a plain-language sentence.

    Never raises and never changes what the rule matches. With no model
    configured, or on any failure, proposals keep their deterministic
    wording — a proposal an operator can read is worth more than no
    proposal at all.
    """
    if not proposals:
        return proposals

    try:
        from aiops_api.modules.config import get_effective_config
        from aiops_api.settings import get_settings

        config = get_effective_config(tenant_id)
        model = config.llm_model or get_settings().llm_model
        api_key = config.llm_api_key or None
    except Exception:  # noqa: BLE001
        logger.warning("could not read agent config for proposal wording", exc_info=True)
        return proposals

    if not model:
        # No LLM configured: deterministic wording is the product, not a
        # degraded mode.
        return proposals

    samples_by_cel = samples_by_cel or {}
    for proposal in proposals:
        try:
            _apply_one(proposal, samples_by_cel.get(proposal.cel, []), model, api_key)
        except Exception as exc:  # noqa: BLE001 — wording must never fail analysis
            logger.warning(
                "could not word correlation proposal, keeping deterministic text",
                extra={"cel": proposal.cel, "error": f"{type(exc).__name__}: {exc}"},
            )
    return proposals

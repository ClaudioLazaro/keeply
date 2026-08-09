"""A stand-in for the model, so the eval can score the path that ships.

``run_eval`` scored ``deterministic_rca`` — the fallback. Production runs the
LiteLLM path. The two differ in more than the writer: the prompt, the response
parsing, the citation normalisation and the provenance annotation are all
exercised only by the second one. That gap let a real defect through with the
gate green: the prompt carried each evidence item's *summary* and nothing else,
so the model was handed "get_events: 13 events returned" while the fallback,
which reads the whole payload, could see the OOMKilled inside it.

This replaces the network, not the pipeline. Everything upstream and downstream
of the completion runs for real.

The fake answers **from the prompt it is given**, which is the point. It quotes
back the evidence detail it can find and cites the markers it can see, so a
prompt that stops carrying detail produces a thin summary that fails the
keyword rubric. A fake returning a canned reply would keep passing and would
measure nothing.
"""

from __future__ import annotations

import json
import re
import sys
import types
from contextlib import contextmanager

# `[E1] get_events: 13 events returned` followed by an indented detail line.
_ITEM_RE = re.compile(
    r"^\[(?P<marker>[EK]\d+)\]\s*(?P<head>.*?)$"
    r"(?:\n\s{4,}(?P<detail>.*?)$)?",
    re.MULTILINE,
)


def _harvest(prompt: str) -> list[dict[str, str]]:
    """Every cited item in the prompt, with whatever detail accompanied it."""
    found = []
    for match in _ITEM_RE.finditer(prompt):
        found.append(
            {
                "marker": match.group("marker"),
                "head": (match.group("head") or "").strip(),
                "detail": (match.group("detail") or "").strip(),
            }
        )
    return found


def _respond(prompt: str) -> str:
    """Build a reply grounded in what the prompt actually carried."""
    items = _harvest(prompt)
    evidence = [i for i in items if i["marker"].startswith("E")]
    knowledge = [i for i in items if i["marker"].startswith("K")]

    # Detail first: it holds the findings. Falling back to the head means the
    # prompt gave us only counts, and the summary will be correspondingly
    # empty of anything a rubric can recognise.
    substance = " ".join(i["detail"] or i["head"] for i in evidence)[:1200]

    refs = [i["marker"] for i in evidence[:3]] or ["E1"]
    krefs = [i["marker"] for i in knowledge[:1]]

    return json.dumps(
        {
            "summary": (
                "Based on the collected evidence: " + substance
                if substance
                else "No evidence detail was provided."
            ),
            "hypotheses": [
                {
                    "title": "Primary cause indicated by the gathered evidence",
                    "confidence": 0.7,
                    "evidence_refs": refs,
                    "knowledge_refs": krefs,
                },
                {
                    "title": "Secondary contributing factor",
                    "confidence": 0.3,
                    "evidence_refs": refs[:1],
                    "knowledge_refs": [],
                },
            ],
        }
    )


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Usage:
    total_tokens = 0


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]
        self.usage = _Usage()


def completion(*_args, messages: list[dict] | None = None, **_kwargs) -> _Response:
    """Signature-compatible with ``litellm.completion`` for our call site."""
    user = ""
    for message in messages or []:
        if message.get("role") == "user":
            user = message.get("content", "")
    return _Response(_respond(user))


@contextmanager
def installed():
    """Put the fake where ``engine._call_llm`` will import it.

    The call site does ``import litellm`` inside the function, so swapping the
    entry in ``sys.modules`` is enough and nothing in the engine needs a seam
    added for testability.
    """
    previous = sys.modules.get("litellm")
    module = types.ModuleType("litellm")
    module.completion = completion  # type: ignore[attr-defined]
    sys.modules["litellm"] = module
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = previous

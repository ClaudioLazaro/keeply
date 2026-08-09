"""Strip credentials out of tool output before it leaves the server.

Container logs, Slack messages and issue bodies routinely carry tokens, keys
and connection strings. That text becomes evidence: it is stored in the
investigation payload, rendered to the operator, and sent to whichever LLM
provider is configured. None of those is a place a production credential
should end up, and none of them can be un-sent.

Applied here, at the server, rather than in the coordinator — the earlier the
better, because every layer downstream is one more copy to reason about.

**This is mitigation, not a guarantee.** A secret in a format nobody
anticipated passes through. Saying so plainly matters: the failure mode of a
redactor is believing the problem is solved.

The opposite error is worse for this product. Over-redacting removes the
finding an investigation exists to surface, and a redacted RCA looks exactly
as confident as a complete one — the same trap as stub evidence rendering
like live telemetry. So every pattern here is anchored and specific, never a
loose "long random-looking string", and every removal is **marked** with what
it was, so the reader knows something stood there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Each pattern keeps whatever identifies the field and replaces only the
# secret, so the shape of the log line survives.
_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "bearer-token",
        re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9\-._~+/]{16,}=*"),
        r"\1[redacted: bearer-token]",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "[redacted: jwt]",
    ),
    (
        "aws-access-key",
        re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA)[A-Z0-9]{16}\b"),
        "[redacted: aws-access-key]",
    ),
    (
        "private-key",
        re.compile(
            r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[redacted: private-key]",
    ),
    (
        "url-credentials",
        # postgres://user:secret@host -> keeps the user, drops the password.
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+):([^\s@/]{3,})@"),
        r"\1:[redacted: url-password]@",
    ),
    (
        "assigned-secret",
        # password=..., api_key: "...", token => ... — the identifier is what
        # makes this safe to match; the value alone would be guesswork.
        re.compile(
            # Bare `token` matters most and was missing: it is the commonest
            # form by far, and the suite passed because the tests only covered
            # the variants that had been written.
            r"(?i)\b(pass(?:word|wd)?|passphrase|secret|token|api[_-]?key|apikey"
            r"|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key"
            r"|credential)"
            r"(\s*[:=]{1,2}>?\s*)"
            r"([\"']?)([^\s\"',;}{]{6,})\3"
        ),
        r"\1\2\3[redacted: \1]\3",
    ),
)


@dataclass(frozen=True)
class Redaction:
    text: str
    removed: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.removed)


def redact(text: str) -> Redaction:
    """Remove credential-shaped values, reporting what was removed."""
    if not text:
        return Redaction(text=text, removed=())
    removed: list[str] = []
    for label, pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        if count:
            removed.extend([label] * count)
    return Redaction(text=text, removed=tuple(removed))


def redact_lines(lines: list[str]) -> tuple[list[str], tuple[str, ...]]:
    """Redact a block of log lines, keeping their order and count."""
    out: list[str] = []
    removed: list[str] = []
    for line in lines:
        result = redact(line)
        out.append(result.text)
        removed.extend(result.removed)
    return out, tuple(removed)

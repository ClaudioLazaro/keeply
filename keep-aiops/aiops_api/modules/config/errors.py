"""Validation-error redaction for credential-shaped fields.

FastAPI's default 422 body includes an ``input`` key echoing the offending
value. For a field whose whole purpose is to reject a pasted API key, that
means the rejected key is returned to the caller — and lands in access
logs, browser devtools, and any error-reporting pipeline. Rejecting the
write is not enough; the value must not come back.
"""

import re
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

REDACTED = "[redacted]"

# Fields whose submitted value must never be echoed back.
SENSITIVE_FIELDS = frozenset({"llm_api_key_env", "llm_api_key", "api_key", "token", "secret"})

# Anything shaped like a live credential, wherever it appears.
#
# Naming the field is not sufficient. A nested object — the per-function
# assistant settings are one — is echoed back whole when validation rejects
# the parent, so a key pasted into any leaf would be returned even though
# every individual field name is innocuous. The value's own shape is the
# only property that travels with it, so that is what gets checked.
CREDENTIAL_SHAPED = re.compile(r"^(sk-|xox[baprs]-|ghp_|AKIA|Bearer\s)", re.IGNORECASE)

# The same shapes, but matched anywhere inside a longer string. Pydantic
# messages routinely quote the value that failed, so an anchored pattern
# alone would clear the `input` key and leave the secret sitting in `msg`.
CREDENTIAL_INLINE = re.compile(r"(sk-|xox[baprs]-|ghp_|AKIA|Bearer\s)\S+", re.IGNORECASE)


def _is_sensitive(location: Any) -> bool:
    if not isinstance(location, (list, tuple)):
        return False
    return any(isinstance(part, str) and part in SENSITIVE_FIELDS for part in location)


def scrub(value: Any) -> Any:
    """Replace credential-shaped strings anywhere inside a value.

    Structure is preserved so the rest of the error stays useful — the
    operator still sees which function and which field they got wrong.
    """
    if isinstance(value, str):
        if CREDENTIAL_SHAPED.match(value):
            return REDACTED
        return CREDENTIAL_INLINE.sub(REDACTED, value)
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return value


def redact_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip echoed input from errors on sensitive fields.

    Also normalises ``ctx``: pydantic puts the raw exception object there,
    which is not JSON-serialisable and would turn a 422 into a 500.
    """
    redacted: list[dict[str, Any]] = []
    for error in errors:
        sensitive = _is_sensitive(error.get("loc"))
        item: dict[str, Any] = {
            "type": error.get("type"),
            "loc": list(error.get("loc") or []),
            "msg": error.get("msg"),
        }
        if "input" in error:
            item["input"] = REDACTED if sensitive else scrub(error["input"])
        ctx = error.get("ctx")
        if isinstance(ctx, dict):
            # The message pydantic builds may quote the offending value, so
            # it is scrubbed too rather than trusted.
            item["ctx"] = {
                key: REDACTED if sensitive else scrub(str(value)) for key, value in ctx.items()
            }
        if isinstance(item.get("msg"), str):
            item["msg"] = scrub(item["msg"])
        redacted.append(item)
    return redacted


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=422, content={"detail": redact_validation_errors(exc.errors())})

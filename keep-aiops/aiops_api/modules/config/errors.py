"""Validation-error redaction for credential-shaped fields.

FastAPI's default 422 body includes an ``input`` key echoing the offending
value. For a field whose whole purpose is to reject a pasted API key, that
means the rejected key is returned to the caller — and lands in access
logs, browser devtools, and any error-reporting pipeline. Rejecting the
write is not enough; the value must not come back.
"""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

REDACTED = "[redacted]"

# Fields whose submitted value must never be echoed back.
SENSITIVE_FIELDS = frozenset({"llm_api_key_env", "llm_api_key", "api_key", "token", "secret"})


def _is_sensitive(location: Any) -> bool:
    if not isinstance(location, (list, tuple)):
        return False
    return any(isinstance(part, str) and part in SENSITIVE_FIELDS for part in location)


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
            item["input"] = REDACTED if sensitive else error["input"]
        ctx = error.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = {
                key: REDACTED if sensitive else str(value) for key, value in ctx.items()
            }
        redacted.append(item)
    return redacted


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del request
    return JSONResponse(status_code=422, content={"detail": redact_validation_errors(exc.errors())})

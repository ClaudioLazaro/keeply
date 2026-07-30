"""Policy module: persisted, tenant-aware, fail-closed (ADR-0003).

M1 persists policies (``models.py``) and evaluates them via
``engine.evaluate``: tenant-specific enabled policies override the global
``"*"`` policy; nothing matching means deny. The seeded global policy keeps
the M0 suggest-only posture — allow read, deny mutate.

``assert_tool_allowed`` remains the orchestrator's enforcement point with its
original signature; only an ``allow`` decision passes. If the policy store is
unreachable the static M0 default applies (allow read, deny everything
else), which is itself fail-closed for mutations.
"""

import logging
from pathlib import Path

from aiops_api.modules.policy.engine import (
    ALLOW,
    APPROVAL_REQUIRED,
    DENY,
    PolicyDecision,
    evaluate_with_session,
)
from aiops_api.modules.policy.models import GLOBAL_TENANT
from aiops_api.settings import get_settings

logger = logging.getLogger(__name__)


class PolicyDenied(Exception):
    """Raised when a tool invocation is rejected by policy."""

    def __init__(self, tool_name: str, reason: str) -> None:
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"policy denied tool '{tool_name}': {reason}")


READ_EXECUTION_CLASS = "read"


def _static_default(execution_class: str | None) -> str:
    """M0 static default used when the policy store is unreachable."""
    return ALLOW if execution_class == READ_EXECUTION_CLASS else DENY


def _sqlite_file_missing() -> bool:
    """True when the store is a SQLite file that does not exist yet.

    Checking first avoids creating a stray DB file on every fail-closed
    fallback (e.g. unit tests running without the app lifespan).
    """
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        return False
    path = url[len("sqlite:///"):]
    return path != ":memory:" and not Path(path).exists()


def assert_tool_allowed(tool_name: str, execution_class: str | None) -> None:
    """Enforce policy on a tool call; raise PolicyDenied unless allowed."""
    try:
        if _sqlite_file_missing():
            outcome = PolicyDecision(decision=_static_default(execution_class), policy_id=None)
        else:
            outcome = evaluate_with_session(GLOBAL_TENANT, tool_name, execution_class or "")
    except Exception:
        logger.warning("policy store unreachable; using static suggest-only default", exc_info=True)
        outcome = PolicyDecision(decision=_static_default(execution_class), policy_id=None)
    if outcome.decision == ALLOW:
        return
    if outcome.decision == APPROVAL_REQUIRED:
        reason = f"tool requires approval (policy '{outcome.policy_id}')"
    elif outcome.policy_id is not None:
        reason = f"execution_class {execution_class!r} denied by policy '{outcome.policy_id}'"
    else:
        reason = f"execution_class {execution_class!r} matched no policy rule (fail-closed default)"
    raise PolicyDenied(tool_name, reason)

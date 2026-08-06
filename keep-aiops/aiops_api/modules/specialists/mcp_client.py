"""MCP client for the federated tool mesh (ContextForge).

Presents the same ``(catalog, invoke)`` pair the coordinator already consumes,
so swapping transports does not touch a single specialist.

Three things stay on this side of the boundary, deliberately:

* **Policy.** Fail-closed, and grounded in our own allowlist rather than in
  what a tool claims about itself. A mesh federates servers we do not control,
  so a tool asserting ``readOnlyHint`` proves nothing; ContextForge also drops
  annotations in transit, so believing them would deny everything anyway.
  Anything outside the allowlist is ``mutate``, which the suggest-only policy
  refuses.
* **Budget.** Tool-call counting is investigation semantics, not a gateway
  feature.
* **Provenance.** ``structured_content.backend`` is authoritative because the
  server that talked to the backend is the only party that knows. A tool that
  reports nothing is classified ``unknown``, never ``live``.

MCP is session-oriented and async; the orchestrator is synchronous and runs in
a worker thread. An anyio blocking portal bridges the two, holding one session
open for the whole gathering phase — which is also the right lifetime, since a
session is the unit MCP itself is built around.
"""

from __future__ import annotations

import logging
import re
from fnmatch import fnmatch
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

READ = "read"
MUTATE = "mutate"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def canonical(name: str) -> str:
    """Fold a tool name to a separator-insensitive key.

    Federation renames things: our server publishes ``get_pods`` and
    ContextForge exposes it as ``keeply-k8s-get-pods``. Canonicalising both
    lets one comparison cover hyphen/underscore and case differences.
    """
    return _NON_ALNUM.sub("_", name.lower()).strip("_")


def _aliases(name: str) -> list[str]:
    """Progressively shorter trailing names, longest first.

    ``keeply-k8s-get-pods`` yields ``keeply_k8s_get_pods``, ``k8s_get_pods``,
    ``get_pods``, ``pods`` — so a specialist asking for ``get_pods`` finds the
    federated tool without every specialist learning the gateway's naming.
    """
    parts = canonical(name).split("_")
    return ["_".join(parts[i:]) for i in range(len(parts))]


def build_alias_map(names: list[str]) -> dict[str, str]:
    """Map every unambiguous alias to its real federated tool name.

    An alias claimed by two tools is dropped rather than guessed: with a
    Kubernetes and a Prometheus server both federated, a bare ``query`` must
    fail to resolve instead of silently picking one. Specialists then have to
    name the tool fully, which is the correct outcome.
    """
    claims: dict[str, set[str]] = {}
    for name in names:
        for alias in _aliases(name):
            claims.setdefault(alias, set()).add(name)
    resolved = {alias: next(iter(owners)) for alias, owners in claims.items() if len(owners) == 1}
    dropped = sorted(alias for alias, owners in claims.items() if len(owners) > 1)
    if dropped:
        logger.info("ambiguous tool aliases dropped; call these by full name", extra={"aliases": dropped})
    return resolved


_NON_ALNUM_KEEP_GLOB = re.compile(r"[^a-z0-9*?]+")


def _canonical_pattern(pattern: str) -> str:
    """Canonicalise an allowlist pattern without eating its wildcards.

    ``canonical`` folds every non-alphanumeric run to ``_``, which would turn
    ``keeply-k8s-*`` into ``keeply_k8s`` and quietly match nothing.
    """
    return _NON_ALNUM_KEEP_GLOB.sub("_", pattern.lower()).strip("_")


def _matches(patterns: list[str], name: str) -> bool:
    subject = canonical(name)
    return any(fnmatch(subject, _canonical_pattern(p)) for p in patterns)


def execution_class(tool: Any, trusted_read_only: list[str] | None = None) -> str:
    """Decide the policy class for a federated tool. Fail-closed.

    Privilege comes from **our** allowlist, never from the tool. The MCP spec
    says annotations from untrusted servers must be treated as untrusted, and
    a mesh federates servers we do not control — a hostile one could simply
    declare itself read-only. So ``read`` requires that the tool match a
    pattern we configured, asserting that we know what that server does.

    Annotations still matter, but only downward: a tool that declares itself
    destructive is demoted even if our allowlist covers it. This mirrors the
    provenance rule elsewhere in the codebase — confidence is only ever
    reduced, never inflated.

    A further reason this cannot rest on annotations: ContextForge drops them
    while federating. Our own tools arrive with ``read_only_hint=None``
    despite declaring it, so an annotation-only gate would deny everything.
    """
    annotations = getattr(tool, "annotations", None)
    if annotations is not None and getattr(annotations, "destructive_hint", None) is True:
        return MUTATE
    name = getattr(tool, "name", "") or ""
    return READ if _matches(trusted_read_only or [], name) else MUTATE


def unwrap_structured(payload: Any) -> Any:
    """Return the tool's own object, unwrapping a gateway envelope if present.

    ContextForge may nest the result under a lone ``result`` key. Unwrapping is
    conditional on that being the *only* key, so a legitimate payload that
    happens to carry a ``result`` field is left alone.
    """
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


def _tool_result(result: Any) -> Any:
    """Best available representation of a CallToolResult.

    Prefers ``structured_content`` because provenance lives there. Falls back
    to concatenated text content so a server without an output schema still
    produces evidence — classified ``unknown``, since it proved nothing about
    where the data came from.
    """
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return unwrap_structured(structured)
    blocks = getattr(result, "content", None) or []
    text = "\n".join(getattr(b, "text", "") for b in blocks if getattr(b, "text", None))
    return {"text": text} if text else None


@contextmanager
def mcp_mesh(
    *,
    url: str,
    token: str,
    tenant_id: str,
    investigation_id: str,
    timeout: float,
    trusted_read_only: list[str],
) -> Iterator[tuple[dict[str, dict[str, Any]], Any]]:
    """Open one MCP session and yield ``(catalog, invoke)``.

    ``catalog`` keys are the federated tool names plus their unambiguous
    aliases, so it stays a drop-in for the coordinator's membership checks.
    """
    import httpx2
    from anyio.from_thread import start_blocking_portal
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from aiops_api.modules.policy import assert_tool_allowed

    headers = {
        "Authorization": f"Bearer {token}",
        # Carried for the mesh's own audit trail; the gateway records who asked.
        "X-Keeply-Tenant": tenant_id,
        "X-Keeply-Investigation": investigation_id,
    }

    @contextmanager
    def _session():
        with start_blocking_portal() as portal:
            http = portal.call(lambda: httpx2.AsyncClient(headers=headers, timeout=timeout))
            try:
                with portal.wrap_async_context_manager(
                    streamable_http_client(url, http_client=http)
                ) as streams:
                    with portal.wrap_async_context_manager(
                        ClientSession(streams[0], streams[1])
                    ) as session:
                        portal.call(session.initialize)
                        yield portal, session
            finally:
                portal.call(http.aclose)

    with _session() as (portal, session):
        listed = portal.call(session.list_tools)
        by_name: dict[str, dict[str, Any]] = {}
        for tool in listed.tools:
            by_name[tool.name] = {
                "name": tool.name,
                "execution_class": execution_class(tool, trusted_read_only),
                "description": tool.description,
                "input_schema": getattr(tool, "input_schema", None),
                # Advisory only. The result's own `backend` decides provenance;
                # a catalog cannot know whether a given call reached anything.
                "mode": "unknown",
            }

        catalog: dict[str, dict[str, Any]] = dict(by_name)
        for alias, real in build_alias_map(list(by_name)).items():
            catalog.setdefault(alias, by_name[real])

        logger.info(
            "mcp mesh ready",
            extra={
                "investigation_id": investigation_id,
                "tools": len(by_name),
                "read_only": sum(1 for d in by_name.values() if d["execution_class"] == READ),
            },
        )

        def invoke(tool: str, arguments: dict[str, Any]) -> tuple[Any, str | None]:
            descriptor = catalog.get(tool)
            assert_tool_allowed(tool, descriptor.get("execution_class") if descriptor else None)
            real_name = descriptor["name"] if descriptor else tool
            result = portal.call(lambda: session.call_tool(real_name, arguments))
            if getattr(result, "is_error", False):
                # A protocol-level tool error is a gap, and the specialist's
                # caller records it as one; raising keeps that path identical
                # to the legacy transport's failure handling.
                raise RuntimeError(_tool_result(result) or "tool reported an error")
            # MCP has no audit id; the mesh keeps its own trail keyed by session.
            return _tool_result(result), None

        yield catalog, invoke

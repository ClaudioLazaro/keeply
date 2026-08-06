"""The MCP client's three judgement calls: naming, policy class, provenance."""

from types import SimpleNamespace

import pytest

from aiops_api.modules.specialists.mcp_client import (
    MUTATE,
    READ,
    build_alias_map,
    canonical,
    execution_class,
    unwrap_structured,
    _tool_result,
)


TRUSTED = ["keeply-k8s-*"]


def _tool(name="keeply-k8s-get-pods", **annotations):
    return SimpleNamespace(
        name=name,
        annotations=SimpleNamespace(**annotations) if annotations else None,
    )


# --------------------------------------------------------------------------- #
# Naming across federation
# --------------------------------------------------------------------------- #


def test_canonical_folds_separators_and_case():
    assert canonical("keeply-k8s-get-pods") == "keeply_k8s_get_pods"
    assert canonical("Get_Pods") == "get_pods"


def test_specialists_keep_their_logical_tool_names():
    """A specialist asks for get_pods; federation published something longer."""
    aliases = build_alias_map(["keeply-k8s-get-pods", "keeply-k8s-get-logs"])
    assert aliases["get_pods"] == "keeply-k8s-get-pods"
    assert aliases["get_logs"] == "keeply-k8s-get-logs"
    # The full name still resolves to itself.
    assert aliases["keeply_k8s_get_pods"] == "keeply-k8s-get-pods"


def test_ambiguous_alias_is_dropped_rather_than_guessed():
    """Two servers, one short name: resolving it would pick a random backend."""
    aliases = build_alias_map(["keeply-k8s-query", "keeply-prom-query"])
    assert "query" not in aliases
    # Disambiguated names still work, so the caller has a way forward.
    assert aliases["k8s_query"] == "keeply-k8s-query"
    assert aliases["prom_query"] == "keeply-prom-query"


# --------------------------------------------------------------------------- #
# Policy class — fail closed
# --------------------------------------------------------------------------- #


def test_an_allowlisted_tool_is_read_even_with_annotations_stripped():
    """ContextForge drops annotations, so the allowlist has to carry it alone."""
    assert execution_class(_tool(), TRUSTED) == READ
    assert execution_class(_tool(read_only_hint=True), TRUSTED) == READ


def test_a_tool_outside_the_allowlist_is_mutate_however_it_advertises_itself():
    """A federated server we did not vet cannot grant itself privilege."""
    stranger = _tool(name="somebody-elses-tool", read_only_hint=True)
    assert execution_class(stranger, TRUSTED) == MUTATE


def test_a_destructive_declaration_demotes_an_allowlisted_tool():
    """Annotations may only reduce privilege, never grant it."""
    assert execution_class(_tool(destructive_hint=True), TRUSTED) == MUTATE


@pytest.mark.parametrize(
    "annotations",
    [{}, {"read_only_hint": None}, {"read_only_hint": False}, {"read_only_hint": True}],
    ids=["absent", "unstated", "not-read-only", "claims-read-only"],
)
def test_an_empty_allowlist_admits_nothing(annotations):
    """Adding a server must be a deliberate act, not a default."""
    assert execution_class(_tool(**annotations), []) == MUTATE


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


def test_gateway_envelope_is_unwrapped():
    assert unwrap_structured({"result": {"backend": "live"}}) == {"backend": "live"}


def test_a_payload_that_merely_has_a_result_field_is_left_alone():
    """Unwrapping unconditionally would mangle a tool's own `result` key."""
    payload = {"result": "ok", "backend": "live"}
    assert unwrap_structured(payload) == payload


def test_structured_content_is_preferred_because_provenance_lives_there():
    result = SimpleNamespace(
        structured_content={"backend": "live", "cluster": "prod"},
        content=[SimpleNamespace(text="ignored")],
        is_error=False,
    )
    assert _tool_result(result)["backend"] == "live"


def test_text_only_server_still_yields_evidence():
    """No output schema means no provenance claim — but not no evidence.

    The coordinator classifies this `unknown`; the point is that it never
    becomes `live` by default.
    """
    result = SimpleNamespace(
        structured_content=None,
        content=[SimpleNamespace(text="line one"), SimpleNamespace(text="line two")],
        is_error=False,
    )
    assert _tool_result(result) == {"text": "line one\nline two"}
    assert "backend" not in _tool_result(result)

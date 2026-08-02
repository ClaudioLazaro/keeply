"""M3 coordinator: runs specialists under a budget, persists evidence,
breaks the run on breach, and never raises out of a specialist crash.

The coordinator's own budget tracking is exercised via a real built-in
specialist (BackstageSpecialist — single tool, deterministic payload) so
the test mirrors production wiring.
"""

from dataclasses import dataclass
from typing import Any

import pytest

from aiops_api.modules.orchestrator.models import Evidence
from aiops_api.modules.specialists.base import Budget, BudgetExceeded, SpecialistResult
from aiops_api.modules.specialists.builtin import BackstageSpecialist, JiraSpecialist, _safe_call
from aiops_api.modules.specialists.coordinator import run_specialists
from aiops_api.modules.specialists.tracker import BudgetTracker


@dataclass
class _FakeResponse:
    status_code: int = 200
    _json: Any = None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> Any:
        return self._json


class _CatalogClient:
    """Minimal httpx-shaped stand-in: GET for the catalog, POST for invokes."""

    def __init__(self, catalog: list[dict[str, Any]], invoke_payloads: dict[str, Any]):
        self._catalog = catalog
        self._invoke_payloads = invoke_payloads
        self.calls: list[tuple[str, str | None]] = []

    def get(self, url: str):
        if url.endswith("/v1/mcp/tools"):
            return _FakeResponse(200, self._catalog)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, json: dict[str, Any]):
        # URL like http://mcp/v1/mcp/tools/<tool>:invoke
        tool = url.rsplit("/", 1)[-1].split(":")[0]
        self.calls.append((tool, json.get("investigation_id")))
        payload = self._invoke_payloads.get(tool)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            return _FakeResponse(200, {"result": {}, "audit_id": f"audit-{tool}"})
        return _FakeResponse(200, {"result": payload, "audit_id": f"audit-{tool}"})


def test_coordinator_persists_evidence_per_specialist(monkeypatch):
    catalog = [
        {"name": "backstage_get_entity", "execution_class": "read", "input_schema": {}},
    ]
    payloads = {"backstage_get_entity": {"entity": {"name": "payment-api"}}}
    client = _CatalogClient(catalog, payloads)
    monkeypatch.setattr(
        "aiops_api.modules.specialists.coordinator.httpx.Client",
        lambda timeout: client,
    )

    evidence, results, tracker = run_specialists(
        investigation_id="inv-1",
        tenant_id="tenant-1",
        gateway_url="http://mcp",
        budget=Budget(tool_calls=10, wall_time=10.0, llm_tokens=10_000),
        specialists=(BackstageSpecialist(),),
    )
    assert len(evidence) == 1
    assert isinstance(evidence[0], Evidence)
    assert evidence[0].tool == "backstage_get_entity"
    assert "audit-backstage_get_entity" in str(evidence[0].payload.get("audit_id"))
    assert tracker.tool_calls == 1
    assert results[0].specialist == "backstage"


def test_coordinator_budget_breach_propagates(monkeypatch):
    catalog = [
        {"name": "jira_search_issues", "execution_class": "read", "input_schema": {}},
    ]
    client = _CatalogClient(catalog, {"jira_search_issues": {"issues": []}})
    monkeypatch.setattr(
        "aiops_api.modules.specialists.coordinator.httpx.Client",
        lambda timeout: client,
    )

    class _LoopingJira(JiraSpecialist):
        """Override to call ``_safe_call`` 10x against a 3-call budget."""

        def gather(self, *, catalog, invoke, budget, used):
            calls = []
            for _ in range(10):
                calls.append(
                    _safe_call("jira_search_issues", {"jql": "project = PAY"}, invoke, used)
                )
            return SpecialistResult(specialist=self.name, calls=calls)

    with pytest.raises(BudgetExceeded):
        run_specialists(
            investigation_id="inv-2",
            tenant_id="tenant-1",
            gateway_url="http://mcp",
            budget=Budget(tool_calls=3, wall_time=10.0, llm_tokens=10_000),
            specialists=(_LoopingJira(),),
        )


def test_coordinator_swallows_specialist_crash(monkeypatch):
    catalog = [
        {"name": "backstage_get_entity", "execution_class": "read", "input_schema": {}},
    ]
    client = _CatalogClient(catalog, {"backstage_get_entity": {"entity": {}}})
    monkeypatch.setattr(
        "aiops_api.modules.specialists.coordinator.httpx.Client",
        lambda timeout: client,
    )

    class _Crashy(BackstageSpecialist):
        def gather(self, *, catalog, invoke, budget, used):
            raise ValueError("specialist impl bug")

    evidence, results, _ = run_specialists(
        investigation_id="inv-3",
        tenant_id="tenant-1",
        gateway_url="http://mcp",
        budget=Budget(tool_calls=10, wall_time=10.0, llm_tokens=10_000),
        specialists=(_Crashy(),),
    )
    assert len(evidence) == 1
    assert "specialist impl bug" in evidence[0].summary
    assert results[0].specialist == "backstage"

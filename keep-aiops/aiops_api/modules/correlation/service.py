"""Correlation analysis: read alert history, propose rules for Keep's engine.

Implements the client half of Keep's external-AI contract. Keep reminds us
about a tenant; we hold the issued back-API key and analyse on our own
schedule, reading alerts through Keep's public API.

**Nothing here creates incidents.** Keep's rules engine already does that
— synchronously on the ingestion path, with grouping criteria, approval
gating, auto-resolution and name templates. Running a second correlator
beside it would mean two paths creating incidents from the same alerts.
What Keep cannot do is work out which rule to write, so that is the whole
job: find the groupings that keep recurring and propose them as rules an
operator reviews in /rules.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlmodel import Session, select

from aiops_api.db import get_engine, session_scope
from aiops_api.modules.correlation.grouping import CorrelationGroup, group_alerts
from aiops_api.modules.correlation.models import (
    CorrelationClient,
    RuleSuggestion,
    _utcnow,
)
from aiops_api.modules.correlation.rules import RuleProposal, propose_rules
from aiops_api.modules.correlation.wording import apply_wording

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20.0

# How many correlation windows of history to analyse. A pattern has to
# repeat to become a rule, so one window is never enough to judge.
HISTORY_WINDOWS = 30

# Defaults mirror config_default in keep/api/models/db/ai_external.py. They
# only apply until Keep hands us the tenant's actual settings.
DEFAULT_SETTINGS: dict[str, Any] = {
    # Analysis is read-only, but it still costs API calls against Keep, so
    # it stays off until asked for.
    "Enabled": False,
    "Correlation Window (minutes)": 10.0,
    "Similarity Threshold": 0.6,
    "Minimum Occurrences": 2.0,
    "Max Alerts Per Incident": 20.0,
}

ALGORITHM_ID = "Keeply Alert Correlation_1"


def register_client(tenant_id: str, back_api_url: str, back_api_key: str) -> None:
    """Record (or refresh) a tenant Keep asked us to correlate for."""
    with session_scope() as session:
        client = session.get(CorrelationClient, tenant_id)
        if client is None:
            client = CorrelationClient(
                tenant_id=tenant_id,
                back_api_url=back_api_url,
                back_api_key=back_api_key,
            )
            logger.info("correlation client registered", extra={"tenant_id": tenant_id})
        else:
            # Keep rotates the key; always take the latest.
            client.back_api_url = back_api_url
            client.back_api_key = back_api_key
            client.last_reminded_at = _utcnow()
        session.add(client)


def active_clients() -> list[CorrelationClient]:
    with Session(get_engine()) as session:
        clients = session.exec(
            select(CorrelationClient).where(CorrelationClient.enabled.is_(True))
        ).all()
        for client in clients:
            session.expunge(client)
        return list(clients)


def _client_http(client: CorrelationClient) -> httpx.Client:
    return httpx.Client(
        base_url=client.back_api_url.rstrip("/"),
        headers={"X-API-KEY": client.back_api_key, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )


def fetch_config(client: CorrelationClient) -> dict[str, Any] | None:
    """This algorithm's whole config row as Keep holds it.

    Returned verbatim rather than reduced to settings, because writing
    execution logs back means echoing every other field untouched — Keep's
    update replaces the row wholesale.
    """
    try:
        with _client_http(client) as http:
            response = http.get("/ai/stats")
            response.raise_for_status()
            payload = response.json()
    except Exception:  # noqa: BLE001
        logger.warning("could not read correlation config", exc_info=True)
        return None

    for config in payload.get("algorithm_configs") or []:
        if config.get("algorithm_id") == ALGORITHM_ID:
            return config
    return None


def fetch_settings(client: CorrelationClient) -> dict[str, Any]:
    """The tenant's settings as configured on the AI page.

    Falls back to defaults when Keep is unreachable or the algorithm has no
    stored config — running with defaults beats not running at all, and the
    defaults are the conservative end of every range.
    """
    return settings_from_config(fetch_config(client))


def settings_from_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Read the setting values out of a config row, ignoring anything the
    algorithm does not understand."""
    settings = dict(DEFAULT_SETTINGS)
    if not config:
        return settings
    for item in config.get("settings") or []:
        name, value = item.get("name"), item.get("value")
        if name not in settings:
            continue
        # bool before number: `isinstance(True, int)` is True in Python, so
        # checking numbers first would turn Enabled into 1.0.
        if isinstance(value, bool):
            settings[name] = value
        elif isinstance(value, (int, float)):
            settings[name] = float(value)
    return settings


def _decoded(value: Any) -> Any:
    """Unwrap a value that was serialised into JSON text one or more times.

    Returned unchanged when it is not JSON text, so a genuine string field
    stays a string.
    """
    while isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            break
    return value


def report_execution(client: CorrelationClient, config: dict[str, Any] | None, message: str) -> None:
    """Write what this pass did back onto Keep's AI page.

    Without this the page reads "Algorithm not executed yet" forever, which
    is indistinguishable from the plugin being broken — the operator has no
    way to tell a correlator that found nothing from one that never ran.

    The config is re-read immediately before writing rather than reused from
    the start of the run: Keep replaces the whole row, so a settings change
    made while the analysis was in flight would otherwise be silently
    reverted. Re-reading narrows that window to the width of one request.
    Never raises — failing to write a log must not fail the analysis.
    """
    try:
        fresh = fetch_config(client) or config
        if not fresh or not fresh.get("id"):
            logger.warning("no correlation config to report execution onto")
            return
        body = dict(fresh)
        # Echo structured fields as structures. A deployment carrying rows
        # from the old double-encoding bug hands these back as JSON text;
        # writing that text straight back would nest it one level deeper
        # every run. Decoding first makes this pass repair rather than rot.
        for key in ("settings", "settings_proposed_by_algorithm"):
            body[key] = _decoded(body.get(key))
        body["feedback_logs"] = message
        with _client_http(client) as http:
            response = http.put(f"/ai/{ALGORITHM_ID}/settings", json=body)
            response.raise_for_status()
    except Exception:  # noqa: BLE001
        logger.warning("could not report correlation execution to Keep", exc_info=True)


def fetch_recent_alerts(client: CorrelationClient, window_minutes: float) -> list[dict[str, Any]]:
    """Alerts that arrived inside the correlation window.

    The window is widened slightly so an alert near the boundary can still
    join a group formed just before it.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes * 2)
    try:
        with _client_http(client) as http:
            response = http.get("/alerts", params={"limit": 500})
            response.raise_for_status()
            payload = response.json()
    except Exception:  # noqa: BLE001
        logger.warning("could not read alerts for correlation", exc_info=True)
        return []

    alerts = payload if isinstance(payload, list) else payload.get("items") or []
    fresh: list[dict[str, Any]] = []
    for alert in alerts:
        raw = alert.get("lastReceived") or alert.get("last_received")
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if when >= cutoff:
            fresh.append(alert)
    return fresh


def _existing_signature(session: Session, tenant_id: str, cel: str) -> bool:
    """Whether this exact rule was already proposed.

    Without it the analysis would re-queue the same suggestion on every
    run, and a dismissed proposal would keep coming back.
    """
    return (
        session.exec(
            select(RuleSuggestion)
            .where(RuleSuggestion.tenant_id == tenant_id)
            .where(RuleSuggestion.cel == cel)
        ).first()
        is not None
    )


def store_proposals(
    client: CorrelationClient,
    proposals: list[RuleProposal],
    settings: dict[str, Any],
) -> int:
    """Queue new proposals; returns how many were actually new."""
    stored = 0
    with session_scope() as session:
        for proposal in proposals:
            if _existing_signature(session, client.tenant_id, proposal.cel):
                continue
            session.add(
                RuleSuggestion(
                    tenant_id=client.tenant_id,
                    name=proposal.name,
                    cel=proposal.cel,
                    grouping_criteria=proposal.grouping_criteria,
                    timeframe_seconds=proposal.timeframe_seconds,
                    occurrences=proposal.occurrences,
                    alerts_covered=proposal.alerts_covered,
                    rationale=proposal.rationale,
                    settings_snapshot=dict(settings),
                )
            )
            stored += 1
    return stored


def accept_suggestion(suggestion_id: str) -> dict[str, Any]:
    """Turn a suggestion into a real Keep rule.

    The rule is created through Keep's own API with `requireApprove`, so
    the first incidents it produces are candidates a human confirms. From
    that point the suggestion is inert — Keep's engine owns the behaviour,
    and the rule is edited or deleted in /rules like any other.
    """
    with session_scope() as session:
        suggestion = session.get(RuleSuggestion, suggestion_id)
        if suggestion is None:
            raise LookupError(suggestion_id)
        if suggestion.status != "pending":
            return {"status": suggestion.status, "rule_id": suggestion.created_rule_id}
        tenant_id = suggestion.tenant_id
        payload = {
            "ruleName": suggestion.name,
            "celQuery": suggestion.cel,
            "timeframeInSeconds": suggestion.timeframe_seconds,
            "timeUnit": "seconds",
            "groupingCriteria": list(suggestion.grouping_criteria or []),
            "requireApprove": True,
            "createOn": "any",
            "resolveOn": "never",
            "groupDescription": suggestion.rationale,
        }

    client = next((c for c in active_clients() if c.tenant_id == tenant_id), None)
    if client is None:
        raise LookupError(f"no correlation client for tenant {tenant_id}")

    with _client_http(client) as http:
        response = http.post("/rules/from-cel", json=payload)
        response.raise_for_status()
        rule = response.json()

    rule_id = str(rule.get("id") or "")
    with session_scope() as session:
        stored = session.get(RuleSuggestion, suggestion_id)
        if stored is not None:
            stored.status = "accepted"
            stored.created_rule_id = rule_id
            stored.decided_at = _utcnow()
            session.add(stored)

    logger.info(
        "correlation suggestion accepted",
        extra={"suggestion_id": suggestion_id, "rule_id": rule_id},
    )
    return {"status": "accepted", "rule_id": rule_id}


def dismiss_suggestion(suggestion_id: str) -> dict[str, Any]:
    """Reject a proposal. It is kept, not deleted, so the analysis does not
    propose it again on the next run."""
    with session_scope() as session:
        suggestion = session.get(RuleSuggestion, suggestion_id)
        if suggestion is None:
            raise LookupError(suggestion_id)
        suggestion.status = "dismissed"
        suggestion.decided_at = _utcnow()
        session.add(suggestion)
    return {"status": "dismissed"}


def _summarise(
    started: datetime,
    alerts: list[dict[str, Any]],
    groups: list[CorrelationGroup],
    stored: int,
    pending: int,
    qualified: int | None = None,
) -> str:
    """A plain-language account of one pass, for the AI page.

    The interesting cases are the quiet ones: an operator who sees groups
    found but nothing proposed should be told it was the recurrence
    threshold, not left to guess whether the plugin is broken.
    """
    lines = [
        f"{started:%Y-%m-%d %H:%M UTC} — analysed {len(alerts)} alerts, "
        f"found {len(groups)} correlated group(s)."
    ]

    if stored:
        lines.append(f"Proposed {stored} new correlation rule(s).")
    elif qualified:
        # A steady state, not a problem: the patterns still hold and their
        # proposals are already waiting. Saying only "nothing proposed"
        # would read as a failure.
        lines.append(
            f"Nothing new: the {qualified} qualifying pattern(s) found were "
            "already proposed on an earlier run."
        )
    elif groups:
        lines.append(
            "No new rules proposed: none of these groupings recurred often "
            "enough to clear Minimum Occurrences."
        )
    else:
        lines.append(
            "No new rules proposed: nothing in the history repeated closely "
            "enough to justify one."
        )

    if pending:
        lines.append(f"{pending} proposal(s) awaiting your decision in Rules.")

    # Correlation never creates incidents itself; saying so here stops the
    # page from being read as "this thing is merging my alerts".
    lines.append(
        "This analysis only proposes rules — incidents are created by Keep's "
        "rules engine once you accept one."
    )
    return "\n".join(lines)


def run_for_client(client: CorrelationClient) -> dict[str, int]:
    """One analysis pass for one tenant. Never raises.

    Read-only against Keep: it looks at alert history and queues rule
    proposals. Incidents are created only by Keep's rules engine, and only
    once an operator accepts a proposal.
    """
    config = fetch_config(client)
    settings = settings_from_config(config)
    started = _utcnow()

    if not settings.get("Enabled"):
        logger.info(
            "correlation analysis disabled for tenant, skipping",
            extra={"tenant_id": client.tenant_id},
        )
        # Say so on the page. "Disabled" and "never ran" look identical to
        # an operator otherwise, and only one of them is a problem.
        report_execution(
            client,
            config,
            f"{started:%Y-%m-%d %H:%M UTC} — correlation is switched off. "
            "Turn on Enabled above to start analysing alert history.",
        )
        return {"groups": 0, "proposed": 0}

    window = settings["Correlation Window (minutes)"]
    # Look back far enough to see a pattern repeat — a single grouping is
    # a coincidence, and the whole point is to only propose what recurs.
    alerts = fetch_recent_alerts(client, window * HISTORY_WINDOWS)

    groups = group_alerts(
        alerts,
        window_minutes=window,
        similarity_threshold=settings["Similarity Threshold"],
        max_group_size=int(settings["Max Alerts Per Incident"]),
    )
    proposals = propose_rules(
        groups,
        window_minutes=window,
        min_occurrences=int(settings["Minimum Occurrences"]),
    )
    # Word the proposals with the LLM chosen in Settings -> AI Agents. The
    # model only names and explains: what the rule matches, how often the
    # pattern recurred and whether it qualified are already decided above,
    # and stay decided if the model is absent or fails.
    proposals = apply_wording(
        proposals,
        client.tenant_id,
        {p.cel: p.sample_names for p in proposals},
    )
    stored = store_proposals(client, proposals, settings)

    with session_scope() as session:
        current = session.get(CorrelationClient, client.tenant_id)
        if current is not None:
            current.last_run_at = _utcnow()
            session.add(current)
        pending = len(
            session.exec(
                select(RuleSuggestion)
                .where(RuleSuggestion.tenant_id == client.tenant_id)
                .where(RuleSuggestion.status == "pending")
            ).all()
        )

    report_execution(
        client,
        config,
        _summarise(started, alerts, groups, stored, pending, qualified=len(proposals)),
    )

    logger.info(
        "correlation analysis complete",
        extra={
            "tenant_id": client.tenant_id,
            "alerts": len(alerts),
            "groups": len(groups),
            "proposed": stored,
        },
    )
    return {"groups": len(groups), "proposed": stored}

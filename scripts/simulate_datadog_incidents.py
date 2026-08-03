"""Send a realistic Datadog alert history to Keep.

`scripts/simulate_alerts.py` (Keep's demo mode) mixes six providers and
stamps everything with the current time. That is fine for load, but it
cannot demonstrate correlation: a pattern has to *recur* before it is worth
a rule, and every alert arriving in the same minute is one grouping, not
several.

So this sends Datadog webhook payloads through the real ingestion path
(`POST /alerts/event/datadog`, parsed by the real DatadogProvider) while
backdating them: `last_updated` is what the provider turns into
`lastReceived`, so a multi-hour history can be laid down in one run and the
correlation analysis has something to find immediately.

The scenarios are deliberately mixed, so the analysis is seen deciding
rather than just firing:

* recurring cascades   — the same service failing the same way, repeatedly.
                         These are what should become rule proposals.
* a one-off outage     — never repeats. Correctly proposes nothing, which
                         is the behaviour worth showing off.
* unrelated noise      — different services, different symptoms, so a
                         correlator that grouped on wording alone would
                         merge them and a good one will not.

Usage:

    python scripts/simulate_datadog_incidents.py \
        --url https://keeply.clazar.net/keepapi --api-key "$KEEP_API_KEY"

    # preview without sending
    python scripts/simulate_datadog_incidents.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# Datadog's webhook payload carries tags as one comma-separated string, and
# the provider does `tags_list.remove("monitor")` unconditionally — a payload
# without that literal raises before it is ever parsed.
TAG_MONITOR = "monitor"

# `service` is read from the service: tag and is the strongest correlation
# signal Keep has, so the scenarios below are organised around it.


@dataclass
class Symptom:
    """One alert within a cascade."""

    title: str
    body: str
    query: str
    priority: str = "P2"


@dataclass
class Scenario:
    """A failure pattern, and how often the history should repeat it."""

    service: str
    env: str
    hosts: list[str]
    symptoms: list[Symptom]
    # How many separate times this pattern occurs across the history. Two or
    # more is what makes it a rule proposal rather than a coincidence.
    occurrences: int
    # Minutes between the alerts inside one occurrence. Kept well under the
    # correlation window so they group.
    spread_minutes: int = 4
    note: str = ""
    monitor_ids: list[int] = field(default_factory=list)


SCENARIOS = [
    Scenario(
        service="checkout-api",
        env="production",
        hosts=["srv1-us1-prod", "srv2-us1-prod", "srv3-us1-prod"],
        occurrences=3,
        note="recurring latency cascade — should become a rule proposal",
        symptoms=[
            Symptom(
                "Checkout p99 latency above SLO",
                "p99 latency on checkout-api is 2.4s, SLO is 800ms.",
                "avg(last_5m):p99:trace.http.request{service:checkout-api} > 0.8",
                "P1",
            ),
            Symptom(
                "Checkout 5xx rate rising",
                "5xx rate on checkout-api climbed to 4.2% over 5 minutes.",
                "sum(last_5m):sum:trace.http.request.errors{service:checkout-api}.as_rate() > 0.02",
                "P1",
            ),
            Symptom(
                "Checkout error budget burn rate high",
                "Error budget for checkout-api burning at 14x the sustainable rate.",
                "avg(last_10m):slo_burn_rate{slo_id:checkout-availability} > 10",
                "P2",
            ),
        ],
    ),
    Scenario(
        service="payments-api",
        env="production",
        hosts=["srv1-eu1-prod", "srv2-eu1-prod"],
        occurrences=2,
        note="recurring connection-pool exhaustion — should become a rule proposal",
        symptoms=[
            Symptom(
                "Payments DB connection pool exhausted",
                "payments-api has 0 free connections in the primary pool.",
                "avg(last_5m):avg:postgresql.connections.free{service:payments-api} < 1",
                "P1",
            ),
            Symptom(
                "Payments request queue growing",
                "Pending request queue on payments-api is 840 and rising.",
                "avg(last_5m):avg:app.request.queue.depth{service:payments-api} > 500",
                "P2",
            ),
        ],
    ),
    Scenario(
        service="search-api",
        env="production",
        hosts=["srv1-ap1-prod"],
        occurrences=1,
        note="one-off outage — must NOT become a rule (a single event is not a pattern)",
        symptoms=[
            Symptom(
                "Search cluster node unreachable",
                "elasticsearch node es-3 stopped responding to health checks.",
                "avg(last_5m):avg:elasticsearch.node.up{service:search-api} < 1",
                "P1",
            ),
            Symptom(
                "Search index lag growing",
                "Indexing lag on search-api reached 42 minutes.",
                "avg(last_10m):avg:search.index.lag{service:search-api} > 600",
                "P2",
            ),
        ],
    ),
    Scenario(
        service="notifications-worker",
        env="production",
        hosts=["srv1-us2-prod", "srv2-us2-prod"],
        occurrences=2,
        note="recurring queue backup — a second, unrelated pattern",
        symptoms=[
            Symptom(
                "Notification queue consumer lagging",
                "RabbitMQ consumer for notifications is 12,400 messages behind.",
                "avg(last_5m):avg:rabbitmq.queue.messages{queue:notifications} > 5000",
                "P2",
            ),
            Symptom(
                "Notification delivery latency high",
                "p95 delivery latency for notifications is 46s.",
                "avg(last_5m):p95:notifications.delivery.latency{*} > 30",
                "P3",
            ),
        ],
    ),
    Scenario(
        service="cdn-edge",
        env="staging",
        hosts=["edge1-eu1-stg"],
        occurrences=1,
        note="isolated staging noise — different service, must stay separate",
        symptoms=[
            Symptom(
                "Edge cache hit ratio dropped",
                "Cache hit ratio on cdn-edge fell to 61%.",
                "avg(last_15m):avg:cdn.cache.hit_ratio{env:staging} < 0.8",
                "P3",
            ),
        ],
    ),
]


def build_payload(
    scenario: Scenario, symptom: Symptom, host: str, when: datetime, monitor_id: int
) -> dict:
    """A Datadog webhook payload as the provider expects to receive it."""
    tags = ",".join(
        [
            f"environment:{scenario.env}",
            "team:backend",
            TAG_MONITOR,
            f"service:{scenario.service}",
            f"host:{host}",
        ]
    )
    return {
        "id": f"{monitor_id}",
        "title": f"[{symptom.priority}] {symptom.title}",
        "type": "metric alert",
        "query": symptom.query,
        "message": symptom.body,
        "body": symptom.body,
        "tags": tags,
        "priority": symptom.priority,
        "severity": symptom.priority,
        "monitor_id": str(monitor_id),
        # Fingerprint is (groups, monitor_id), and groups comes from scopes.
        # Distinct pairs keep these as distinct alerts rather than repeated
        # updates of one.
        "scopes": host,
        "alert_transition": "Triggered",
        # The provider reads this (epoch ms) into lastReceived. Backdating it
        # is what lets one run lay down hours of history.
        "last_updated": int(when.timestamp() * 1000),
    }


def plan(hours: int, seed: int) -> list[tuple[datetime, dict, str]]:
    """Lay the scenarios out over the past `hours`, newest last."""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    planned: list[tuple[datetime, dict, str]] = []
    monitor_id = 7000000

    for scenario in SCENARIOS:
        for occurrence in range(scenario.occurrences):
            # Spread occurrences across the window so each forms its own
            # grouping rather than merging into one long one.
            slot = hours * 60 * (occurrence + 1) / (scenario.occurrences + 1)
            started = now - timedelta(minutes=slot + rng.uniform(0, 20))
            for index, symptom in enumerate(scenario.symptoms):
                monitor_id += 1
                when = started + timedelta(minutes=index * scenario.spread_minutes)
                host = scenario.hosts[occurrence % len(scenario.hosts)]
                planned.append(
                    (
                        when,
                        build_payload(scenario, symptom, host, when, monitor_id),
                        scenario.service,
                    )
                )

    planned.sort(key=lambda item: item[0])
    return planned


def send(url: str, api_key: str, payload: dict) -> int:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/alerts/event/datadog",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-API-KEY": api_key,
            # urllib's default User-Agent is rejected by the CDN in front of
            # public deployments (Cloudflare 1010), so identify properly.
            "User-Agent": "keeply-simulator/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        print(f"  ! {exc.code}: {exc.read()[:200].decode(errors='replace')}", file=sys.stderr)
        return exc.code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://keeply.clazar.net/keepapi")
    parser.add_argument("--api-key", default="")
    parser.add_argument(
        "--hours", type=int, default=6, help="How far back the history reaches."
    )
    parser.add_argument("--seed", type=int, default=7, help="Reproducible jitter.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    planned = plan(args.hours, args.seed)

    print(f"{len(planned)} alerts across {len(SCENARIOS)} scenarios, "
          f"spanning the last {args.hours}h\n")
    for scenario in SCENARIOS:
        count = sum(1 for _, _, service in planned if service == scenario.service)
        print(f"  {scenario.service:22} {count:2} alerts  — {scenario.note}")
    print()

    if args.dry_run:
        print("dry run, nothing sent")
        return 0

    if not args.api_key:
        parser.error("--api-key is required unless --dry-run")

    failed = 0
    for when, payload, service in planned:
        status = send(args.url, args.api_key, payload)
        ok = status in (200, 202)
        failed += 0 if ok else 1
        print(f"  {when:%H:%M} {service:22} {payload['title'][:48]:50} {status}")

    print(f"\nsent {len(planned) - failed}/{len(planned)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

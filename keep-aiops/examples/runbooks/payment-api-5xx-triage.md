# payment-api Elevated 5xx Rate Triage

## Symptoms

- AlertManager fires `HighErrorRate` for payment-api: the ratio of HTTP 5xx
  responses exceeds 2% over 5 minutes.
- Clients see intermittent `502 Bad Gateway` from the ingress or `500` from
  the service itself.
- Upstream dependencies (postgres, redis, stripe webhook relay) may also be
  alerting — check before assuming the fault is in payment-api.

## Diagnosis

1. Identify the failing route and status split:
   `sum by (route, status) (rate(http_requests_total{app="payment-api",status=~"5.."}[5m]))`
2. Check pod health: `kubectl get pods -l app=payment-api` — restarts,
   pending pods, or a failing readiness gate all produce 5xx at the ingress.
3. Read recent logs for stack traces:
   `kubectl logs -l app=payment-api --tail=200 --since=15m`
   - `connection refused` / `connection pool exhausted` → database runbook.
   - `timeout` on stripe calls → check the stripe relay and egress proxy.
4. Correlate with the last deploy: `kubectl rollout history deploy/payment-api`.
   Most 5xx spikes within 30 minutes of a rollout are regressions.

## Mitigation

1. If the spike started with a deploy: `kubectl rollout undo deploy/payment-api`.
2. If pods are crashlooping, follow the OOMKilled/restart runbook instead.
3. If a dependency is down, scale payment-api replicas to 1 to stop retry
   storms, then restore the dependency first.

## Escalation

- Page the payments on-call if error rate stays above 5% for 15 minutes or
  any settlement endpoint returns 5xx — settlement failures need same-day
  reconciliation.

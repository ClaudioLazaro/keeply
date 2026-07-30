# Postgres Connection Pool Exhaustion

## Symptoms

- Services log `FATAL: sorry, too many clients` or
  `connection pool exhausted` / `Timeout acquiring connection` errors.
- Prometheus: `pg_stat_activity_count{datname="payments"}` sits at or near
  `max_connections` (default 100).
- Latency climbs on all DB-touching endpoints while CPU and memory look
  normal.

## Diagnosis

1. Count connections by application and state:
   `SELECT application_name, state, count(*) FROM pg_stat_activity GROUP BY 1, 2 ORDER BY 3 DESC;`
2. Look for idle-in-transaction sessions — the classic leak:
   `SELECT pid, application_name, now() - xact_start AS idle_for, query
    FROM pg_stat_activity WHERE state = 'idle in transaction' ORDER BY idle_for DESC;`
3. Check the pool sizing of each client deployment; the sum of all pools
   must stay below `max_connections` minus superuser reserved connections.
4. If a recent deploy added replicas without shrinking per-pod pool size,
   total pool demand likely crossed the server limit.

## Mitigation

1. Kill idle-in-transaction sessions older than 5 minutes:
   `SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE state = 'idle in transaction' AND now() - xact_start > interval '5 minutes';`
2. Reduce per-pod pool size (e.g. `DB_POOL_SIZE=5`) and roll the deployment.
3. If headroom is structurally too small, raise `max_connections` or front
   the database with pgbouncer in transaction pooling mode.

## Prevention

- Budget pools: replicas × pool_size < 80% of max_connections.
- Alert on `idle in transaction` sessions older than 2 minutes.
- Set `idle_in_transaction_session_timeout` (e.g. 60s) on the payments role.

# payment-api OOMKilled / Restart Runbook

## Symptoms

- payment-api pods restart repeatedly; `kubectl get pods` shows increasing
  RESTARTS and last state `OOMKilled` (exit code 137).
- Prometheus: `container_memory_working_set_bytes{app="payment-api"}` climbs
  steadily toward the container memory limit until the kernel OOM killer
  terminates the process.
- AlertManager fires `KubePodOOMKilled` and often `KubePodCrashLooping` for
  the payment-api deployment.

## Diagnosis

1. Confirm the kill reason:
   `kubectl get pod <pod> -o jsonpath='{.status.containerStatuses[*].lastState.terminated.reason}'`
   — must read `OOMKilled`, not `Error` or `Completed`.
2. Check the configured memory limit:
   `kubectl get deploy payment-api -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}'`
3. Plot `container_memory_working_set_bytes` vs the limit over the last 6h.
   A slow, monotonic climb points to a memory leak; a step change after a
   deploy points to a regression in the new build.
4. Pull logs from the previous container instance
   (`kubectl logs <pod> --previous`) and look for heap growth warnings,
   unbounded caches, or large payloads buffered in memory (batch settlement
   files are the usual suspect for payment-api).

## Mitigation

1. Short-term: raise the memory limit by 50% and roll the deployment:
   `kubectl set resources deploy/payment-api --limits=memory=1Gi --requests=memory=512Mi`
2. If restarts continue, roll back to the last known-good image:
   `kubectl rollout undo deploy/payment-api`
3. As a stopgap, a horizontal scale-out spreads heap pressure, but does NOT
   fix a leak — treat it as latency buying, not remediation.

## Prevention

- Add a memory-based HPA alert at 80% of the limit.
- Require load-test memory profiles for payment-api changes touching the
  settlement batch pipeline.
- Keep JVM/Node heap flags (`-Xmx`, `--max-old-space-size`) at ~75% of the
  container limit so off-heap usage cannot trigger OOMKilled.

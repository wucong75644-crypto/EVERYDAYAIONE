# AR-17.2 Runtime Observability Contract

AR-17.2 defines internal contracts only. The current implementation provides a
stable metric catalog, bounded in-memory metrics sink, structured health result,
and alert rule/event contracts. It does not connect Prometheus, Grafana,
Sentry, WeCom or a production alert platform.

## Safety rules

- Labels are limited to `tenant_scope`, `provider`, `executor`, `state`,
  `outcome` and `environment`.
- User, Run, Action, request, prompt, path, token and credential values are not
  labels or payload fields.
- Series and label cardinality are bounded; overflow fails closed.
- Health `ready` is never returned when `production_ready` is false.
- Missing domain sources are `unavailable`, not ready.
- Alert events are deduplicated inside their evaluation window.
- Auto-remediation is forbidden in this contract.
- accepted/unknown alerts only recommend readback/reconcile; they never
  recommend ordinary resubmission.

## Current alert catalogue

The catalogue covers stale heartbeat, readiness degradation, projection dead or
aged backlog, provider reconcile age and revision/idempotency conflicts,
Scheduler CAS conflict, Artifact cleanup failure, Sandbox residue, cost
settlement mismatch, credential/provider readiness failure and tenant kill
switch activation.

The in-memory sinks are disposable-test implementations and always expose
`production_ready = false`. No migration or public API is added by AR-17.2.

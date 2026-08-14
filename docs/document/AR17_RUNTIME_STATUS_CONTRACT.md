# AR-17.1 Runtime Status Contract

`RuntimeStatusSnapshot` is an internal, read-only model for aggregating the
existing Runtime admin status payload and approved domain summaries.

The model is tenant-scoped and redacted. It contains state, counts, timestamps,
versions and stable error codes only. It never exposes request payloads,
provider credentials, tokens, user content, stack traces or filesystem paths.

## State rules

- `ready` means the supplied domain has an approved read-only source and its
  reported readiness is true.
- `degraded` means the source responded but reports an incomplete or unhealthy
  state.
- `unavailable` means no approved read-only source was supplied. It is not
  converted into `ready`.
- `disabled` is used for closed claim gates and production flags.
- `production_enabled` is true only when both the explicit production flag and
  `production_ready` are true.

The current migration-223 admin RPC supplies control, worker, projection and
unknown aggregates. Provider, Scheduler, Artifact, Workspace, Child Run, Cost
and Sandbox domains remain `unavailable` until a tenant-scoped read-only source
is added. This document does not add a migration or public API.

`accepted` and `unknown` are display/reconcile states only. The snapshot has no
resubmit or mutation operation.

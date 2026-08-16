# Agent Runtime production composition and release runbook

This runbook is the operating contract for migration 223. It does not authorize
a production release. Every switch in `agent_runtime_control` is initially
false; installing code or starting a service therefore cannot transfer an
owner.

## Current and target call chains

Current web traffic is prepared by
`api.routes.message_chat_preparation.prepare_and_start_chat_generation`,
delivered by
`api.routes.ws.websocket_endpoint`, and normally enqueued to the Conversation
Actor. WeCom uses `services.wecom.actor_enqueue.enqueue_wecom_message`. Legacy
`services.handlers.chat_tool_mixin` may execute SAFE tools only; it returns
`RUNTIME_OWNER_REQUIRED` for every non-SAFE tool, including `code_execute`.

When both the local ingress flag and the PostgreSQL organization gate are open,
web ingress calls `RuntimeIngress.submit`, then the atomic
`runtime_submit_ingress` RPC. WeCom uses
`enqueue_wecom_runtime_turn_v3`, which stores the inbound item, Session,
Command and legacy projection anchors in one transaction. A repeated
idempotency key returns the same Session, Command and Run receipt, including
after the original HTTP/WebSocket response was lost.

The dedicated runtime process composes
`RuntimeCoordinator -> RuntimeLoopCoordinator -> ModelLoopDriver /
ActionLoopDriver`. It claims durable Commands and Runs; it never depends on the
request process remaining alive. Model calls are fenced by durable attempt
facts. An accepted or unknown attempt is reconcile-only. Actions pass through
`PostgresActionAuthorizationRepository` and
`gate_agent_action_dispatch`; a Redis confirmation can only resolve its bound
Authorization Interaction and can never bypass the PostgreSQL PolicyReceipt or
Dispatch Gate. The Projection process claims an Interaction notification lease,
creates/delivers the Redis challenge, and records only delivery completion; the
API response path alone invokes `resolve_agent_tool_confirmation_v3`.
`code_execute` only creates a Sandbox Job. The independent
Sandbox Worker is the sole nsjail owner. Projection and Authorization Recovery
run in separate processes and scan durable work after restart.

## Unique owner and permission matrix

| Process | May claim | Narrow RPCs | Forbidden data/authority | Side effects | Redis | Workspace/OSS |
|---|---|---|---|---|---|---|
| API Backend (`everydayai-api` / DB `everydayai_runtime`) | no Runtime work | runtime ingress, V3 Interaction resolution, capability report | no Command/Run/Action/Sandbox claim or dispatch | ingress response only | confirmation V3 probe/response only | existing request capabilities only; no Sandbox ownership |
| Conversation Actor (`everydayai-actor` / DB `everydayai_runtime`) | legacy actor tasks only | existing actor capabilities | no Runtime/Sandbox claim or non-SAFE dispatch | SAFE legacy tools only | existing actor queue | existing actor contract |
| WeCom Runtime (`everydayai-wecom` / DB `everydayai_wecom_runtime`) | inbound WeCom item only | atomic WeCom V3 ingress, V3 Interaction resolution | no Runtime/Sandbox claim or non-SAFE dispatch | channel ingress/egress only | existing channel/actor wake only | existing channel contract |
| Agent Runtime Worker (`everydayai-agent-runtime` / DB `everydayai_agent_runtime_worker`) | Commands, Runs, model/action attempts | migration 223 runtime allow-list | no projection/admin/Sandbox execution tables; no direct confirmation authority | model provider and authorized action adapter only | none | stage Sandbox inputs; no Sandbox process |
| Projection Worker (`everydayai-agent-projection` / DB `everydayai_projection_worker`) | projection outbox and open-Interaction notification leases | projection claim/project/fail/dead RPCs; narrow Tool Confirmation notification claim/complete RPCs | no Runtime, Authorization or Sandbox execution claims | compatibility DB projection and V3 challenge delivery only; never resolves or dispatches an Action | Tool Confirmation V3 namespace and distributed WebSocket delivery only | none |
| Authorization Recovery (`everydayai-agent-authorization` / DB `everydayai_authorization_worker`) | resolved, approved Authorization Interactions with active grants | authorization recovery RPCs | no action dispatch, model or Sandbox claim | none | none | none |
| Sandbox Worker (`everydayai-sandbox` / DB `everydayai_sandbox_worker`) | local Sandbox Jobs | existing Sandbox Job worker RPC allow-list | no Runtime tables, Redis, model, JWT, WeCom, OSS or application secrets | nsjail only; network denied | none | node-local job store only; no OSS credential |
| Runtime/Admin (`everydayai-api` + DB `everydayai_runtime_admin`) | none | status, control, org rollout, dead projection requeue | no direct table writes and no execution RPCs | control-plane changes only | none | none |

All workers become ready only after configuration, their database connection,
and role-specific capability checks succeed. SIGTERM closes readiness, reports
draining, stops new claims and gives the current fenced operation its systemd
timeout. Backlog does not affect readiness.

## Files and process composition

- `backend/services/agent/runtime/composition.py`: four process-exclusive
  composition roots.
- `backend/agent_runtime_worker_main.py`: gate polling, heartbeat, Unix health,
  drain and shutdown.
- `backend/services/agent/runtime/production_model.py`: fenced context,
  tenant-bound provider call and deterministic Action creation.
- `backend/services/agent/runtime/ingress.py`: idempotent durable ingress.
- `backend/services/agent/runtime/application/confirmation_notification.py`:
  leased delivery of persisted Interactions as V3 challenges.
- `backend/api/routes/runtime_admin.py`: super-admin, tenant-scoped admin RPCs.
- `backend/services/tool_confirmation/*`: Redis V3 binding/probe; PostgreSQL
  remains authoritative.
- `backend/services/agent/runtime/sandbox/*`: workspace, nsjail, cancellation,
  quarantine and cleanup.
- `backend/migrations/223_agent_runtime_production_composition.sql` and its
  rollback: schema, ACLs, gates, audit and failure-closed rollback.
- `deploy/everydayai-agent-*.service`,
  `deploy/everydayai-sandbox-worker.service`: non-root process roles.
- `deploy/bootstrap-agent-runtime-roles.sh` creates the four active Runtime
  LOGIN roles. The historical Gateway bootstrap is retained only so a disposable
  database can traverse frozen 227_18 migration history; it does not provision an
  active service or current execution owner.

## Environment and secrets

Install `/etc/everydayai/*.env` from `deploy/env-templates`, owned by root and
mode 0640. Runtime loads `agent-runtime-worker.env` and the two-key
`agent-runtime-model.env`. The model file is `root:everydayai-runtime-model-secret`;
only the Runtime user joins that group. It contains KEK metadata only, never a
Provider API key. Projection, Authorization and Sandbox cannot read it.
The Sandbox template is deliberately limited to its database URL,
immutable hashes, local paths, limits and Sentry; it must not contain Redis,
OSS, model, JWT or WeCom values. `RUNTIME_ADMIN_DATABASE_URL` is available only
to the API process. Local ingress flags and every database control switch stay
false until the ordered cutover.

## Image v13 production activation

The image release is isolated from chat `v1`. After the release commit has
passed migration and worker checks, run
`deploy/enable-agent-runtime-media.sh --expected-sha <release-sha>`. The
script atomically provisions the Runtime and Projection media env flags,
restarts only those two workers, waits for a fresh Projection heartbeat, and
then calls the audited `set_agent_runtime_media_production_state_v1` RPC. That
RPC uses a state-version compare-and-set, records an immutable admin audit
entry, and enables only `everydayai-default/v13` for image ingress. Failure
rolls the env transaction back and leaves image ingress closed; chat `v1` is
not changed.

## Sandbox production contract

The binary is nsjail 3.4 commit
`079d70dda4aa1edd9512cfd25ff1e47e316dc355`, installed at the fixed path by
`deploy/install_nsjail.sh` and verified by SHA-256. The private GitHub Release
rootfs workflow builds Ubuntu 24.04 with Python 3.12 and the minimal package
set, emits a complete file manifest and artifact SHA-256, and never consumes a
mutable `latest` artifact. `deploy/install-sandbox-rootfs.sh` verifies all
release checksums and the complete manifest, installs a new immutable revision
atomically, and never overwrites an existing revision. The Sandbox systemd
mount namespace bind-mounts the whole rootfs store read-only.

`runtime-capability-probe.sh sandbox` verifies the binary, rootfs manifest and
seccomp hashes, cgroup v2 `cpu`, `memory` and `pids` controllers, memory swap
support, concurrency 1, network denial and absence of process/cgroup residue.
The fixed ceilings are 120 seconds, 800m CPU, 512 MiB memory, swap 0, 64 pids,
256 MiB output and 100 files. Startup has no raw-Python fallback.
The jail identity remains `65534:65534` inside the namespace and is mapped only
to the actual non-root Sandbox Worker UID/GID captured by production
composition. Root startup, a different process role, or an unsafe workspace
owner fails closed.

`/var/lib/everydayai/sandbox-jobs` is root-owned, setgid to
`everydayai-sandbox-io`; only Runtime and Sandbox users share that group. Inputs
are immutable. Outputs remain node-local. Cancellation must terminate the
entire nsjail process tree and prove cleanup. Partials are quarantined for
exactly 24 hours; cleanup failure is fatal and must page. A failed or stale
probe/heartbeat keeps `code_execute_enabled` closed. Single-node storage means
there is no cross-node takeover.

## Health, readiness, management state and alerts

Each worker exposes only a Unix socket below its systemd RuntimeDirectory. A
healthy reply is `ready=true,draining=false`; it means the process can safely
accept work, not that the queues are empty. The super-admin status RPC contains
worker heartbeats, Redis capability, projection backlog/dead/oldest time and
unknown/total counts.

Release gates and alerts, never readiness, consume those values:

- missing/stale worker heartbeat: page immediately;
- Redis capability false/stale or Sandbox probe false/stale: page and keep
  non-SAFE/code execution closed;
- any dead projection: page;
- oldest projection pending age over 5 minutes or rising backlog for 10
  minutes: warn; over 15 minutes: page;
- any accepted/unknown older than its reconciliation SLO: page; unknown ratio
  above 1% with at least 20 attempts blocks rollout;
- quarantine cleanup failure or residual nsjail/cgroup: page immediately.

All worker fatal errors are structured Loguru/journald errors and Sentry
exceptions. Before canary, use a non-production deliberate probe event and
verify receipt in Sentry and the existing error-alert delivery channel. Never
put payload, arguments, credentials or Sandbox stdout in an alert. Immutable
admin audit rows supply actor, tenant, request id, reason and result.

## Ordered release

Single-Runtime code validation is flags-off only. The reviewed control-plane
manifest contains exactly Runtime, Projection and Authorization units.
Provisioning stages four env files in one release transaction; any env publish,
unit install, daemon-reload or state postcheck failure restores all envs and
units. Runtime must be `inactive:disabled`, all production flags remain false,
and Sandbox assets must have zero diff. The disposable workflow validates 227_53,
the direct model adapter, existing Runtime recovery contracts and mock Providers.
It does not consume production `DATABASE_URL` and does not authorize installation,
migration, service start or Owner switch.

Before any install, the historical `everydayai-agent-model-gateway` unit must be
`inactive:not-found`, and its two env files must be absent. The installer fails
closed when legacy assets remain; retirement is a separate reviewed, recoverable
operation and is never performed implicitly by provisioning.

1. Bootstrap and verify the four active Runtime database LOGIN roles; inject
   secrets outside migration tooling. Create the historical Gateway role only
   inside disposable migration-chain verification when 227_18 requires it.
2. Apply migration 223. Verify all control switches are false and ACL tests
   pass.
3. Run the Redis V3 capability probe.
4. On Linux run the pinned nsjail/rootfs/seccomp/cgroup probe.
5. Install/start new Workers with database gates closed.
6. Enable Projection and Authorization Recovery; observe heartbeats and queues.
7. Close legacy non-SAFE intake and drain for 60 minutes. On timeout, keep
   ingress closed; do not force failure or redispatch.
8. Deploy Backend, Actor and WeCom with local ingress flags still false, then
   open their database-controlled owner path.
9. Deploy the V3 frontend only after all Backend/Actor instances speak V3. An
   old frontend must fail closed.
10. Add one organization to the rollout whitelist and enable ingress/claims.
11. Observe health, backlog, oldest age, unknown ratios and dead stream.
12. Expand the organization whitelist, then complete the cutover.
13. Permanently close/remove old execution paths only after the observation
   window. The old ToolLoop may never regain `code_execute`; ordinary Workers
   may never regain dispatch.

## Rollback and recovery

- **Runtime model rollback:** keep production flags false, drain without
  redispatching `dispatching` ambiguity, and restore only through the
  release-bound env/unit journal. Preserve ModelAttempt facts; 227_53 rollback
  is disposable-only and fails when captured epoch facts exist.

- **Migration rollback:** run the exact 223 rollback only before any Runtime,
  Authorization, Projection, Sandbox, heartbeat, capability or admin facts
  exist. Its guard rejects rollback before any ACL change otherwise. A clean
  rollback grants no compatibility projection mutation helper to `PUBLIC`,
  ordinary Runtime or the legacy Worker.
- **Application version:** close ingress/claim/dispatch first, drain, then roll
  back binaries compatible with schema 223. Never restore legacy non-SAFE
  execution.
- **Owner switch:** remove organizations from rollout and close ingress/claim.
  Let accepted/unknown work reconcile under the durable owner.
- **Frontend protocol:** roll back only with non-SAFE confirmation disabled.
  V2/V3 owners may not coexist.
- **Redis outage:** confirmation and non-SAFE gates remain closed. Lost,
  mismatched, repeated, expired, or crash-after-claim challenges execute
  nothing.
- **Sandbox outage:** close `code_execute`; retain durable jobs and reconcile
  on the same node. Never fall back to Python or another node.
- **Accepted/unknown external effects:** reconcile only. Never dispatch again
  because a request timed out or an application version rolled back.

If safety cannot be proven, close ingress and dispatch and continue
reconciliation. That is the only permitted fallback.

## AR-17.1 shared foundation

Migration 224 adds the Web/WeCom v2 ingress contract and Run-bound context
readback. Definition, Catalog, and EffectiveToolset documents are immutable
persisted facts; `enabled_for_new_ingress` is separate from `recoverable`, so
disabling a version does not block an existing Run. Model recovery resolves
those facts from PostgreSQL rather than rebuilding the current process catalog.
The full prompt content, model/context policy, and every catalog safety field
are included in deterministic definition/catalog hashes. Gate changes after
command commit use the stored command envelope for idempotent readback and do
not rewrite the frozen toolset.
It remains disabled by default; AR-17.2--17.4, the 41 professional Executors,
production startup wiring, and production acceptance are not part of this
milestone. Personal `org_id=NULL` ingress remains closed by the organization
rollout whitelist and is an explicit AR-17 completion blocker. Apply runs
`224_01_agent_runtime_ar17_core.sql` followed by
`224_02_agent_runtime_ar17_version_seed.sql`; rollback uses the reverse full
filename order and fails before ACL or
object removal when an ingress-used Runtime fact exists.

## Validation matrix

- Local: unit/integration tests, compile, shell syntax, migration static
  contracts, frontend lint/build/test.
- PostgreSQL: apply 223 to the complete prior schema, test each role's positive
  RPC allow-list and forbidden tables/RPCs, idempotency replay, all-default-off,
  exact rollback on a fact-free DB, and rollback-guard rejection after facts.
- Redis: real Redis V3 probe and contract suite covering missing, wrong binding,
  duplicate, expiry and claim-crash behavior.
- GitHub Linux: rootfs build plus nsjail/cgroup v2/swap/network/cancel/residue,
  systemd unit verification and 24-hour cleanup contract.
- Staging: WebSocket/API/WeCom ingress through Run/Action/Projection,
  confirmation V3, Sandbox, restart scan, drain and response-loss replay.
- Production: read-only role, schema, hashes, cgroup, filesystem ownership,
  systemd dependency, health and alert-delivery preflight only.

No production switch, deployment or side effect is part of code validation.

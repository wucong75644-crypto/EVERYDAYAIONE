# Agent Runtime AR-17.4 生产闭环

AR-17.4 adds an additive 227 lane over the frozen 224–226 contracts.

## Frozen facts

- The production catalog is the union of 18 read Executors, one Sandbox
  Executor, and 23 specialist Executors.  Every entry is Executor-backed and
  has descriptor, schema, safety, executor revision, provider revision,
  readiness hash, and secret-binding facts.
- `ProductionCatalogReceipt` and the production EffectiveToolset include these
  facts in their frozen hashes.  A missing binding, secret, readiness hash, or
  unavailable Executor fails closed.
- Historical Runs continue to restore the database definition/catalog/toolset
  facts.  New ingress uses the additive v3 RPC and the final database receipt;
  Web and WeCom use the same contract.

## Rollout and shadow

Rollout is subject-scoped by organization or user and channel.  A personal
conversation (`org_id IS NULL`) is eligible only through its explicit user
subject; it is never treated as global.  The default is disabled.  Shadow
comparison is pure: it records definition/toolset/policy/argument/executor/
projection mismatches and performs no model call, provider submission, cost,
workspace write, or terminal transition.

## Release and rollback

Apply `227_01_agent_runtime_production_closure.sql` after 224–226.  Rollback
requires no persisted shadow facts, production bindings, or enabled rollout
subjects, then drops only the 227 objects.  No production deployment,
credential, owner switch, or feature enablement is part of AR-17.4.

# Native development bootstrap validation — 2026-10-17

Base: `f7c3be32b6cb23293de170b39a0392d239cc43bf`; repair branch `codex/task/20261017190352-cloud-schema-repair`. No commit, push, merge, deployment or production connection.

## Results

- Backend focused regression: **242 passed**, two existing pytest configuration/Pydantic deprecation warnings. Suites cover local async DB/RPC, Redis websocket, org scope/isolation/upsert, permissions/auth/conversations, task recovery and message gateway. This is a focused regression run, not the entire backend test collection.
- Bootstrap/native integration and cloud/migration executor tooling: **19 passed** (8 new native integration, 6 cloud development, 5 migration executor). Combined: **261 tests passed**.
- Real API smoke: **10 checks passed**: health, DB health, authenticated identity, organization creation/list/members, conversation create/list/get and nonmember 403. Non-superuser app account, real SQL rollback and authenticated Redis DB 15 checks passed.
- Current-run backend log observation: **20 seconds**, zero ERROR/missing table/column/function/permission errors. Lifespan recovery and organization initialization/scanning run against the actual bootstrap schema. External model/SMS/OSS calls are not configured or validated; workers, sandbox and external automation remain disabled.
- Two independent fresh databases initialize from the baseline successfully. Repeat bootstrap preserves inserted data. Unknown nonempty DB and catalog/ACL drift are refused. Injected SQL failure rolls back all structure/receipt and a retry succeeds. No historical applied ledger is fabricated.
- Stop/start of PostgreSQL and Redis preserves independently written markers; a synthetic application record also survives service restart. A separate checkout-path fixture has a distinct PostgreSQL system identifier, separate Redis process/DB 15, empty user table and identical bootstrap catalog. Fixture services were stopped after verification.
- The source compiler completed again on a fresh build database; its independently restored catalog is byte-identical to the frozen catalog. All 229 original SQL/source checksums and production migration runner remain unchanged.
- Dependency setup with `CLOUD_PYTHON=python3.12 bash scripts/cloud/setup.sh --with-services` succeeded. Runtime versions: Python 3.12.14, Node 24.19.0, npm 11.9.0; pinned native PostgreSQL 17.11, pgvector 0.8.0, Redis 8.0.2; maintenance parser pglast 8.6. Exact native package versions/URLs/SHA256 are in `native-packages.json`; existing Python requirements and frontend lockfile are unchanged.
- Frontend evidence retained from the same unchanged base: 177 test files / 1834 tests and production build passed. No frontend application code changed in this repair; the full frontend suite was not rerun for the schema-only repair.

## Reproduction and scope

Follow CLOUD_SCHEMA_BOOTSTRAP.md for source provenance, tests, candidate regeneration and safe task startup. Test output, local secrets, synthetic account/organization IDs and connection metadata stay in ignored/private development paths and are excluded from the distributable patch. The downloadable patch includes this report, schema/catalog, source manifest, exact native dependency pins, scripts and tests.

## Published snapshot follow-up — 2026-10-17

A separate fresh published VM was subsequently verified in task `codex/task/20261017193909-published-snapshot-validation`. Its task-specific state directory and application database did not exist, and no data-service listeners were running before setup. `setup.sh --with-services` returned `initialized-empty`; a second invocation returned `verified-existing`. Complete frontend/backend startup and the ten-check real API smoke passed three rounds with zero errors in each 20-second observation. The original focused backend regression (242 tests), expanded backend regression (278 tests), 19 bootstrap/tooling tests, all 177 frontend test files / 1834 tests, and frontend build passed. A second task verified distinct PostgreSQL system identity, Redis instance and data directories; stop/start preserved synthetic markers. Services were stopped after verification. No production connection or deployment occurred.

The fresh-VM report remains at `/workspace/work/snapshot-validation/VALIDATION_REPORT.md` in that cloud snapshot. Network authorization was required for fetch/TestClient, and frontend testing was rerun successfully after dependency installation completed. These are cloud integration results, not claims of native Linux integration being rerun on macOS.

## Local synchronization — 2026-10-18

The complete cloud patch was retrieved with SHA256 `cd9714a6fc96cfc1c8d12b15e000155a65815a8e60978e296cca5c4a36def804`, checked against base `f7c3be32b6cb23293de170b39a0392d239cc43bf`, and applied only to the existing independent cloud-development task worktree. Generated SQL comment trailing spaces and terminal blank lines were normalized for the commit whitespace gate; artifact checksums and the export formatter were updated together. SQL statements, catalog, seeds, production migrations and the migration runner were otherwise unchanged. Local validation results are recorded below before the scoped commit/push. No deployment is part of this synchronization.


Local checks: **130 focused backend mock tests passed** (seven files: local DB RPC, websocket Redis, org-scoped DB/filter/isolation/upsert, task recovery, message gateway); **11 cloud/migration tooling tests passed**, with the eight Linux native integration tests explicitly skipped on macOS. The first backend attempt lacked Settings variables; rerunning with synthetic mock-only DATABASE_URL/JWT settings passed. All 229 source checksums, builder/artifact hashes, catalog fingerprint, Python syntax and shell syntax passed. Parsing the cloud-original and whitespace-normalized SQL with pinned pglast 8.6 produced identical ASTs. Task-local log permissions and refusal of a path outside the task state were verified. Native database integration was not rerun locally; the published cloud evidence above remains the integration evidence.

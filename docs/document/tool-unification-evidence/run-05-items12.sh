#!/usr/bin/env bash
# Current item 1/2 regression; synthetic files and a disposable PostgreSQL only.
set -euo pipefail
cd "$(dirname "$0")/../../.."
tool_test_python=${TOOL_TEST_PYTHON:-/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python}
[[ ! -f .env && ! -f backend/.env ]] || { echo 'Refusing checkout with application .env'; exit 1; }
export APP_ENV=testing
export DATABASE_URL=postgresql://tool_test@127.0.0.1:1/tool_test
export JWT_SECRET_KEY=tool-unification-test-only
export REDIS_PORT=1
export PYTHONPATH=backend
evidence=docs/document/tool-unification-evidence
"$tool_test_python" -m pytest \
  backend/tests/test_tool_result_05_production_regressions.py \
  backend/tests/test_file_tool_mixin.py backend/tests/test_attachments_xml.py \
  backend/tests/test_file_tools.py backend/tests/test_file_target_execution.py \
  backend/tests/test_resource_scope_continuity.py backend/tests/test_resource_scope_reproduction.py \
  backend/tests/test_tool_result_consumption.py backend/tests/test_tool_production_integration.py \
  backend/tests/test_scheduled_tasks_routes.py backend/tests/test_scheduled_task_changeset_adapter.py \
  backend/tests/test_tool_registry.py backend/tests/test_chat_tools.py \
  -o addopts='' -v --tb=short > "$evidence/05-items12-regression.txt" 2>&1
command -v initdb >/dev/null
command -v pg_ctl >/dev/null
"$tool_test_python" -m pytest backend/tests/test_scheduled_task_draft_delete_integration.py \
  -o addopts='' -v --tb=short > "$evidence/05-items12-postgres.txt" 2>&1
"$tool_test_python" "$evidence/05-actor-history-probe.py" > "$evidence/05-actor-history-observations.json"
"$tool_test_python" "$evidence/check-05-items12.py" > "$evidence/05-items12-source-checks.json"

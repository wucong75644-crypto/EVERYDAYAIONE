#!/usr/bin/env bash
# Production acceptance fixes. Only synthetic data and local test dependencies.
set -euo pipefail
cd "$(dirname "$0")/../../.."
tool_test_python=${TOOL_TEST_PYTHON:-/Users/wucong/EVERYDAYAIONE/.venv/bin/python}
[[ ! -f .env && ! -f backend/.env ]] || { echo 'Refusing checkout with application .env'; exit 1; }
command -v initdb >/dev/null
command -v pg_ctl >/dev/null
export APP_ENV=testing
export DATABASE_URL=postgresql://tool_test@127.0.0.1:1/tool_test
export JWT_SECRET_KEY=tool-unification-test-only
export REDIS_PORT=1
export MPLCONFIGDIR=/private/tmp/tool05-matplotlib
export IPYTHONDIR=/private/tmp/tool05-ipython
"$tool_test_python" -m pytest \
  backend/tests/test_tool_result_05_production_regressions.py \
  backend/tests/test_context_snapshot.py backend/tests/test_chat_context.py \
  backend/tests/test_attachments_xml.py backend/tests/services/prompt_builder \
  backend/tests/test_chat_execution_engine.py backend/tests/test_chat_stream_loop.py \
  backend/tests/test_tool_result_consumption.py backend/tests/test_tool_production_integration.py \
  backend/tests/test_file_target_execution.py backend/tests/test_resource_scope_continuity.py \
  backend/tests/test_resource_scope_reproduction.py backend/tests/test_chat_tools.py \
  backend/tests/test_file_tools.py backend/tests/test_scheduled_task_changeset_adapter.py \
  backend/tests/test_scheduled_tasks_routes.py backend/tests/test_scheduled_task_workflow.py \
  backend/tests/test_tool_result.py backend/tests/test_tool_execution.py \
  -o addopts='' -v --tb=short -rs > docs/document/tool-unification-evidence/05-fix-regression.txt 2>&1
"$tool_test_python" -m pytest backend/tests/test_scheduled_task_draft_delete_integration.py \
  -o addopts='' -v --tb=short -rs > docs/document/tool-unification-evidence/05-fix-postgres.txt 2>&1

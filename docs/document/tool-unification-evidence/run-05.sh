#!/usr/bin/env bash
# 05: use offline test configuration only, external business providers are mocked.
set -euo pipefail
cd "$(dirname "$0")/../../.."
tool_test_python=${TOOL_TEST_PYTHON:-/Users/wucong/EVERYDAYAIONE/.venv/bin/python}
if [[ -f .env || -f backend/.env ]]; then
    echo 'Run in a test checkout without .env; do not load production configuration.' >&2
    exit 1
fi
export APP_ENV=testing
export DATABASE_URL=postgresql://tool_test:tool_test@127.0.0.1:1/tool_test
export JWT_SECRET_KEY=tool-unification-test-only
export REDIS_PORT=1
export MPLCONFIGDIR=/private/tmp/tool05-matplotlib
export IPYTHONDIR=/private/tmp/tool05-ipython
"$tool_test_python" -m pytest \
    backend/tests/test_tool_result_consumption.py \
    backend/tests/test_tool_execution.py backend/tests/test_tool_result.py \
    backend/tests/test_tool_production_integration.py \
    -o addopts='' -v --tb=short > docs/document/tool-unification-evidence/05-integration.txt 2>&1
"$tool_test_python" -m pytest \
    backend/tests/test_tool_executor.py backend/tests/test_agent_result.py \
    backend/tests/test_agent_protocol_integration.py backend/tests/test_chat_generate_mixin.py \
    backend/tests/test_tool_loop_tooloutput.py backend/tests/test_file_tool_mixin.py \
    backend/tests/test_file_id_protocol.py backend/tests/test_file_id_e2e.py \
    backend/tests/test_sandbox_tool_mixin.py backend/tests/test_media_tool_executor.py \
    backend/tests/test_chat_tools.py backend/tests/test_common_tools.py backend/tests/test_file_tools.py \
    backend/tests/test_code_tools.py backend/tests/test_agent_tools.py backend/tests/test_phase_tools.py \
    backend/tests/test_chat_tool_mixin.py backend/tests/test_chat_tool_loop.py \
    backend/tests/test_tool_loop_helpers.py backend/tests/test_tool_loop_parallel.py \
    backend/tests/test_tool_result_cache.py backend/tests/test_scheduled_task_agent.py \
    backend/tests/test_scheduled_task_workflow.py backend/tests/test_tool_result_envelope.py \
    backend/tests/test_tool_output.py backend/tests/test_tool_invocation_store.py \
    backend/tests/test_tool_audit.py backend/tests/test_loop_hooks.py backend/tests/test_stop_policy.py \
    backend/tests/test_scheduled_task_agent_integration.py backend/tests/test_chat_execution_engine.py \
    backend/tests/test_chat_stream_loop.py backend/tests/test_chat_stream_runner.py \
    backend/tests/test_chat_stream_session.py backend/tests/test_chat_actor_sink.py \
    backend/tests/test_tool_loop_failure_outcome.py backend/tests/test_tool_loop_context.py \
    backend/tests/test_tool_loop_usage.py backend/tests/test_chat_task_manager.py \
    backend/tests/test_emit_protocol.py backend/tests/test_emit_three_engines.py \
    backend/tests/test_emit_auto_hooks.py \
    -o addopts='' -v --tb=short > docs/document/tool-unification-evidence/05-regression.txt 2>&1
# Existing ERP test modifies sys.modules during collection; use its own process.
"$tool_test_python" -m pytest backend/tests/test_erp_tool_mixin_unit.py \
    -o addopts='' -v --tb=short > docs/document/tool-unification-evidence/05-erp.txt 2>&1

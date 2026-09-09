#!/usr/bin/env bash
# Block 04: production entrypoint integration plus existing contracts; mock business IO only.
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
"$tool_test_python" -m pytest \
    backend/tests/test_tool_execution.py backend/tests/test_tool_result.py \
    backend/tests/test_tool_production_integration.py \
    backend/tests/test_tool_policy.py backend/tests/test_tool_registry.py \
    -o addopts='' -v --tb=short > docs/document/tool-unification-evidence/04-core.txt 2>&1
"$tool_test_python" -m pytest \
    backend/tests/test_tool_executor.py \
    backend/tests/test_agent_result.py \
    backend/tests/test_agent_protocol_integration.py \
    backend/tests/test_chat_generate_mixin.py \
    backend/tests/test_tool_loop_tooloutput.py \
    backend/tests/test_file_tool_mixin.py \
    backend/tests/test_file_id_protocol.py \
    backend/tests/test_file_id_e2e.py \
    backend/tests/test_sandbox_tool_mixin.py \
    backend/tests/test_media_tool_executor.py \
    backend/tests/test_chat_tools.py \
    backend/tests/test_common_tools.py \
    backend/tests/test_file_tools.py \
    backend/tests/test_code_tools.py \
    backend/tests/test_agent_tools.py \
    backend/tests/test_phase_tools.py \
    backend/tests/test_channel_context_isolation.py \
    backend/tests/test_execution_scope.py \
    backend/tests/test_permission_mode.py \
    backend/tests/test_tool_args_validator.py \
    backend/tests/test_chat_tool_mixin.py \
    backend/tests/test_chat_tool_loop.py \
    backend/tests/test_tool_loop_helpers.py \
    backend/tests/test_tool_loop_parallel.py \
    backend/tests/test_tool_confirm.py \
    backend/tests/test_ws_tool_confirmation.py \
    backend/tests/test_tool_result_cache.py \
    backend/tests/test_scheduled_task_agent.py \
    backend/tests/test_scheduled_task_workflow.py \
    backend/tests/test_planner_framework.py \
    backend/tests/test_tool_result_envelope.py \
    backend/tests/test_tool_output.py \
    backend/tests/test_tool_invocation_store.py \
    backend/tests/test_tool_audit.py \
    backend/tests/test_scheduled_task_agent_integration.py \
    backend/tests/test_chat_execution_engine.py \
    backend/tests/test_chat_stream_loop.py \
    backend/tests/test_chat_stream_runner.py \
    backend/tests/test_chat_stream_session.py \
    backend/tests/test_chat_actor_sink.py \
    backend/tests/test_conversation_commands.py \
    backend/tests/test_resource_manifest.py \
    backend/tests/test_file_cache_identity.py \
    backend/tests/test_file_tool_boundaries.py \
    backend/tests/test_file_identity.py \
    backend/tests/test_tool_loop_failure_outcome.py \
    backend/tests/test_tool_loop_context.py \
    backend/tests/test_tool_loop_usage.py \
    backend/tests/test_permission_mode_e2e.py \
    backend/tests/test_permissions_v1.py \
    backend/tests/test_permissions_initialization.py \
    backend/tests/test_file_handles_e2e.py \
    backend/tests/test_model_gateway_concurrency_integration.py \
    backend/tests/test_chat_gateway_retry_integration.py \
    backend/tests/test_model_gateway.py \
    backend/tests/test_erp_agent.py \
    backend/tests/test_kuaimai.py \
    backend/tests/test_erp_local.py \
    -o addopts='' -v --tb=short > docs/document/tool-unification-evidence/04-regression.txt 2>&1
# Existing ERP test injects stubs into sys.modules at collection time (block 01 evidence).
"$tool_test_python" -m pytest backend/tests/test_erp_tool_mixin_unit.py \
    -o addopts='' -v --tb=short > docs/document/tool-unification-evidence/04-erp.txt 2>&1

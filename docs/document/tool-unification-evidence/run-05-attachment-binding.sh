#!/usr/bin/env bash
# Offline input-binding and affected compatibility checks. No live model calls.
set -euo pipefail
cd "$(dirname "$0")/../../.."
tool_test_python=${TOOL_TEST_PYTHON:-/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python}
[[ ! -f .env && ! -f backend/.env ]] || { echo 'Refusing checkout with application .env'; exit 1; }
export APP_ENV=testing
export DATABASE_URL=postgresql://tool_test@127.0.0.1:1/tool_test
export JWT_SECRET_KEY=tool-unification-test-only
export REDIS_PORT=1
export PYTHONPATH=backend
"$tool_test_python" -m pytest backend/tests/test_current_attachment_binding.py backend/tests/test_pdf_upload.py backend/tests/test_tool_result_05_production_regressions.py backend/tests/test_attachments_xml.py backend/tests/test_chat_context.py backend/tests/test_context_snapshot.py backend/tests/test_resource_scope_continuity.py backend/tests/test_resource_scope_reproduction.py backend/tests/test_file_target_execution.py backend/tests/test_chat_execution_engine.py backend/tests/test_chat_stream_loop.py backend/tests/test_context_result_restoration.py backend/tests/test_context_compressor.py backend/tests/test_context_compressor_web.py backend/tests/test_dashscope_chat_adapter.py backend/tests/test_openrouter_chat_adapter.py backend/tests/services/prompt_builder backend/tests/test_tool_result_consumption.py backend/tests/test_tool_production_integration.py backend/tests/test_wecom_actor_enqueue.py backend/tests/test_wecom_message_service.py -q -o addopts='' --tb=short -rs > docs/document/tool-unification-evidence/05-attachment-binding-regression.txt 2>&1

#!/usr/bin/env bash
# 附件 ID 修复：临时文件 + mock；不加载生产配置或执行真实业务。
set -euo pipefail
cd "$(dirname "$0")/../../.."
tool_test_python=${TOOL_TEST_PYTHON:-/Users/wucong/EVERYDAYAIONE/.venv/bin/python}
if [[ -f .env || -f backend/.env ]]; then
    echo 'Run in a test checkout without .env.' >&2
    exit 1
fi
export APP_ENV=testing
export DATABASE_URL=postgresql://tool_test:tool_test@127.0.0.1:1/tool_test
export JWT_SECRET_KEY=tool-unification-test-only
export REDIS_PORT=1
"$tool_test_python" -m pytest \
    backend/tests/test_file_cache_identity.py \
    backend/tests/test_file_tool_boundaries.py \
    backend/tests/test_file_id_protocol.py \
    backend/tests/test_file_id_e2e.py \
    backend/tests/test_file_path_cache_analyzed.py \
    backend/tests/test_file_analysis_service.py \
    backend/tests/test_attachments_xml.py \
    backend/tests/test_attachment_routing_baseline.py \
    backend/tests/test_file_tool_mixin.py \
    backend/tests/test_file_executor.py \
    backend/tests/test_file_tools.py \
    backend/tests/test_resource_manifest.py \
    backend/tests/test_channel_context_isolation.py \
    backend/tests/test_chat_tool_mixin.py \
    backend/tests/test_tool_executor.py \
    backend/tests/test_tool_execution.py \
    backend/tests/test_tool_result.py \
    backend/tests/test_tool_registry.py \
    backend/tests/test_tool_policy.py \
    backend/tests/test_sandbox_tool_mixin.py \
    backend/tests/test_media_tool_executor.py \
    backend/tests/test_tool_loop_tooloutput.py \
    backend/tests/test_scheduled_task_agent.py \
    backend/tests/test_scheduled_task_workflow.py \
    backend/tests/test_tool_confirm.py \
    backend/tests/test_ws_tool_confirmation.py \
    backend/tests/test_tool_loop_helpers.py \
    backend/tests/test_chat_tool_loop.py \
    backend/tests/test_department_agents.py \
    backend/tests/test_erp_agent.py \
    backend/tests/test_erp_duckdb_helpers.py \
    -o addopts='' -v --tb=short \
    > docs/document/tool-unification-evidence/03-file-cache-regression.txt 2>&1
"$tool_test_python" - <<'PY' > docs/document/tool-unification-evidence/03-file-cache-checks.txt
import ast
import hashlib
from pathlib import Path
import subprocess

baseline = "e243ba2c0d545d8afca3ae930a40389276b7c123"
expected = {
    "backend/services/agent/file_path_cache.py",
    "backend/services/agent/file_id.py",
    "backend/services/agent/file_analysis_service.py",
    "backend/services/handlers/chat_context/attachments.py",
    "backend/tests/test_file_cache_identity.py",
    "backend/tests/test_file_analysis_service.py",
    "backend/services/agent/file_delete_mixin.py",
    "backend/services/agent/sandbox_tool_mixin.py",
    "backend/services/handlers/chat_tool_helpers.py",
    "backend/tests/test_file_tool_boundaries.py",
    "backend/tests/test_file_id_e2e.py",
    "backend/tests/test_sandbox_tool_mixin.py",
}
def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()

changed = set(git("diff", baseline, "--name-only", "--", "backend").splitlines())
changed.update(git("ls-files", "--others", "--exclude-standard", "backend").splitlines())
assert changed == expected, changed
print("BASE", baseline)
print("HEAD", git("rev-parse", "HEAD"))
print("BRANCH", git("branch", "--show-current"))
for name in sorted(expected):
    data = Path(name).read_bytes()
    ast.parse(data.decode())
    print("SHA256", hashlib.sha256(data).hexdigest(), name)
subprocess.run(["git", "diff", "--check"], check=True)
subprocess.run(["bash", "-n", "docs/document/tool-unification-evidence/run-file-cache-regression.sh"], check=True)
print("PASS: exact authorized backend scope; Python AST; diff whitespace; shell syntax")
print("UNCHANGED: tool schema/ID hash, ToolExecutor public entry, services/tools, WS and persistence sources")
PY

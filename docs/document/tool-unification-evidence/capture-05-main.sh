#!/usr/bin/env bash
# Reproduce the original golden contracts in an isolated, unchanged main export.
set -euo pipefail
cd "$(dirname "$0")/../../.."
reference_dir=$(mktemp -d /private/tmp/tool05-golden.XXXXXX)
git archive 6c0737ab78f2d0cb3b2a8376498e7825b0829431 backend | tar -x -C "$reference_dir"
cp backend/tests/test_tool_result_consumption.py "$reference_dir/backend/tests/"
cd "$reference_dir"
export APP_ENV=testing
export DATABASE_URL=postgresql://tool_test:tool_test@127.0.0.1:1/tool_test
export JWT_SECRET_KEY=tool-unification-test-only
export REDIS_PORT=1
export TOOL05_RECORD_GOLDEN=6c0737ab
"${TOOL_TEST_PYTHON:-/Users/wucong/EVERYDAYAIONE/.venv/bin/python}" -m pytest \
 backend/tests/test_tool_result_consumption.py -k test_live_chat_protocol_matches_main -o addopts='' -v --tb=short
printf 'Original golden fixtures: %s/backend/tests/fixtures/tool_result_05\n' "$reference_dir"

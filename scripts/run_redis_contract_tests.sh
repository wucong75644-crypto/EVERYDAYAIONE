#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDIS_SERVER="${REDIS_SERVER:-$(command -v redis-server || true)}"
REDIS_CLI="${REDIS_CLI:-$(command -v redis-cli || true)}"
PYTHON="${EVERYDAYAI_TEST_PYTHON:-${ROOT_DIR}/backend/venv/bin/python}"

if [[ -z "${REDIS_SERVER}" || -z "${REDIS_CLI}" ]]; then
  echo "redis-server and redis-cli are required" >&2
  exit 2
fi
if [[ ! -x "${PYTHON}" ]]; then
  echo "backend virtualenv not found: ${PYTHON}" >&2
  exit 2
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/everydayai-redis-contract.XXXXXX")"
PORT="$("${PYTHON}" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
PID=""

cleanup() {
  if [[ -n "${PID}" ]] && kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}"
    wait "${PID}" 2>/dev/null || true
  fi
  rm -rf "${TEMP_DIR}"
}
trap cleanup EXIT INT TERM

"${REDIS_SERVER}" \
  --bind 127.0.0.1 \
  --protected-mode yes \
  --port "${PORT}" \
  --dir "${TEMP_DIR}" \
  --dbfilename contract.rdb \
  --save "" \
  --appendonly no \
  --daemonize no \
  >"${TEMP_DIR}/redis.log" 2>&1 &
PID="$!"

for _ in {1..50}; do
  if "${REDIS_CLI}" -h 127.0.0.1 -p "${PORT}" ping >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${PID}" 2>/dev/null; then
    echo "temporary Redis exited before becoming ready" >&2
    exit 1
  fi
  sleep 0.1
done

if [[ "$("${REDIS_CLI}" -h 127.0.0.1 -p "${PORT}" ping)" != "PONG" ]]; then
  echo "temporary Redis did not become ready" >&2
  exit 1
fi

VERSION="$("${REDIS_CLI}" -h 127.0.0.1 -p "${PORT}" INFO server |
  sed -n 's/^redis_version:\(.*\)\r$/\1/p')"
echo "Redis ${VERSION} on 127.0.0.1:${PORT}; dir=${TEMP_DIR}"

(
  cd "${ROOT_DIR}/backend"
  RUN_EXTERNAL_TESTS=1 \
  RUN_REDIS_EXTERNAL_TESTS=1 \
  REDIS_TEST_URL="redis://127.0.0.1:${PORT}/15" \
    "${PYTHON}" -m pytest \
      -o "addopts=-q --tb=short -p no:warnings -p testing.pytest_policy --ignore=tests/manual" \
      -m external tests/test_redis_contract_external.py
)

KEY_COUNT="$("${REDIS_CLI}" -h 127.0.0.1 -p "${PORT}" -n 15 DBSIZE)"
if [[ "${KEY_COUNT}" != "0" ]]; then
  echo "isolated Redis DB has ${KEY_COUNT} residual keys" >&2
  exit 1
fi
echo "Redis contract tests complete; residual_keys=${KEY_COUNT}"

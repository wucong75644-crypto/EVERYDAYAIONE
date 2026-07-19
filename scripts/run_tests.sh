#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
PYTHON="${BACKEND_DIR}/venv/bin/python"
BASE_ADDITIONAL_OPTS="-q --tb=short -p no:warnings -p testing.pytest_policy --ignore=tests/manual"

if [[ ! -x "${PYTHON}" ]]; then
  echo "backend virtualenv not found: ${PYTHON}" >&2
  exit 2
fi

mode="${1:-fast}"
if [[ $# -gt 0 ]]; then
  shift
fi

cd "${BACKEND_DIR}"

case "${mode}" in
  target)
    if [[ $# -eq 0 ]]; then
      echo "usage: scripts/run_tests.sh target <test-path> [pytest args]" >&2
      exit 2
    fi
    normalized_args=()
    for arg in "$@"; do
      if [[ "${arg}" == backend/* ]]; then
        normalized_args+=("${arg#backend/}")
      else
        normalized_args+=("${arg}")
      fi
    done
    exec "${PYTHON}" -m pytest -o "addopts=${BASE_ADDITIONAL_OPTS}" \
      --maxfail=1 "${normalized_args[@]}"
    ;;
  fast)
    exec "${PYTHON}" -m pytest -o "addopts=${BASE_ADDITIONAL_OPTS}" \
      -m "not medium and not large and not external" --durations=10 "$@"
    ;;
  pr)
    exec "${PYTHON}" -m pytest -o "addopts=${BASE_ADDITIONAL_OPTS}" \
      -m "not large and not external" --durations=10 "$@"
    ;;
  full)
    exec "${PYTHON}" -m pytest -o "addopts=${BASE_ADDITIONAL_OPTS}" \
      -m "not external" --durations=20 "$@"
    ;;
  large)
    exec "${PYTHON}" -m pytest -o "addopts=${BASE_ADDITIONAL_OPTS}" \
      -m "large and not external" --durations=20 "$@"
    ;;
  external)
    if [[ "${RUN_EXTERNAL_TESTS:-}" != "1" ]]; then
      echo "external tests require RUN_EXTERNAL_TESTS=1" >&2
      exit 2
    fi
    exec "${PYTHON}" -m pytest -o "addopts=${BASE_ADDITIONAL_OPTS}" \
      -m "external" --durations=20 "$@"
    ;;
  *)
    echo "unknown mode: ${mode} (target|fast|pr|full|large|external)" >&2
    exit 2
    ;;
esac

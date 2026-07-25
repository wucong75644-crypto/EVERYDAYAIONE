#!/bin/bash

set -euo pipefail

if [ -z "${MIGRATION_DATABASE_URL:-}" ] && [ -f ".env.migrator" ]; then
    set -a
    source .env.migrator
    set +a
fi
if [ -z "${MIGRATION_DATABASE_URL:-}" ]; then
    echo "❌ 缺少 MIGRATION_DATABASE_URL，停止部署"
    exit 1
fi

case "${RUN_MIGRATIONS:-false}" in
    true|false) ;;
    *)
        echo "❌ RUN_MIGRATIONS 只能是 true 或 false"
        exit 1
        ;;
esac

migration_python=${MIGRATION_PYTHON:-./venv/bin/python}
if [ ! -x "$migration_python" ]; then
    echo "❌ 迁移 Python 不可执行: ${migration_python}"
    exit 1
fi

migration_plan=$(
    "$migration_python" scripts/migration_runner.py plan \
        --applied-by deploy-script
)
if [ "$RUN_MIGRATIONS" = "true" ]; then
    "$migration_python" scripts/migration_runner.py apply \
        --applied-by deploy-script
elif [ -n "$migration_plan" ]; then
    echo "❌ 存在待执行迁移，RUN_MIGRATIONS=false，停止部署"
    printf '%s\n' "$migration_plan"
    exit 1
fi

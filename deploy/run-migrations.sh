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

if [ -n "${RECONCILE_FAILED_MIGRATION:-}" ]; then
    if [ "${ACKNOWLEDGE_MIGRATION_ROLLBACK:-false}" != "true" ]; then
        echo "❌ 失败迁移恢复必须明确确认事务已回滚"
        exit 1
    fi
    "$migration_python" scripts/migration_runner.py reconcile-failed \
        --identity "$RECONCILE_FAILED_MIGRATION" \
        --acknowledge-transaction-rollback \
        --applied-by deploy-reconciliation
fi

migration_plan=$(
    "$migration_python" scripts/migration_runner.py plan \
        --applied-by deploy-script
)
if printf '%s\n' "$migration_plan" | grep -Fxq \
    '220_platform_admin_credit_adjustment.sql'; then
    database_name=$(
        "$migration_python" -c \
            'import os; from psycopg.conninfo import conninfo_to_dict; print(conninfo_to_dict(os.environ["MIGRATION_DATABASE_URL"])["dbname"])'
    )
    if [[ ! "$database_name" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,62}$ ]]; then
        echo "❌ 迁移数据库名不合法，停止管理员积分 owner 转移"
        exit 1
    fi
    migration_python_dir=$(dirname "$migration_python")
    sudo -n -u postgres env \
        "PATH=${migration_python_dir}:/usr/local/bin:/usr/bin:/bin" \
        "TENANT_DB_ADMIN_URL=postgresql://postgres@%2Fvar%2Frun%2Fpostgresql/${database_name}" \
        bash ../deploy/transfer-admin-credit-adjustment-ownership.sh
fi
if printf '%s\n' "$migration_plan" | grep -Eq \
    '^(171_|172_|173_|174_|175_|176_|177_|178_|179_|180_|181_|182_|183_|184_|185_)'; then
    "$migration_python" scripts/verify_worker_control_preconditions.py
fi
if [ "$RUN_MIGRATIONS" = "true" ]; then
    "$migration_python" scripts/migration_runner.py apply \
        --applied-by deploy-script
elif [ -n "$migration_plan" ]; then
    echo "❌ 存在待执行迁移，RUN_MIGRATIONS=false，停止部署"
    printf '%s\n' "$migration_plan"
    exit 1
fi

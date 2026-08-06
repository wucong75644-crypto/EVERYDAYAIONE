#!/bin/bash

set -euo pipefail

mode=${1:-all}
case "$mode" in all|static-only) ;; *) echo "usage: $0 [all|static-only]" >&2; exit 2 ;; esac

python_bin=${EVERYDAYAI_TEST_PYTHON:-python3}
export PYTHONPATH=${PYTHONPATH:-backend}
export PYTHONDONTWRITEBYTECODE=1

"$python_bin" -m pytest -q -p no:cacheprovider \
  backend/tests/test_agent_runtime_model_gateway_deploy.py \
  backend/tests/test_agent_runtime_control_plane_update.py \
  backend/tests/test_agent_runtime_control_plane_release.py \
  backend/tests/test_agent_runtime_control_plane_env_transaction.py \
  backend/tests/test_agent_runtime_flags_off_install.py \
  backend/tests/test_agent_runtime_model_gateway_local_harness.py \
  backend/tests/test_agent_runtime_model_gateway_protocol.py \
  backend/tests/test_agent_runtime_model_gateway_migration.py \
  backend/tests/test_agent_runtime_model_gateway_predispatch_migration.py \
  backend/tests/test_agent_runtime_model_gateway_dispatch_binding_migration.py \
  backend/tests/test_agent_runtime_model_gateway_repository.py \
  backend/tests/test_agent_runtime_model_gateway_configuration.py \
  backend/tests/test_agent_runtime_model_gateway_process.py \
  backend/tests/test_agent_runtime_model_gateway_service.py \
  backend/tests/test_agent_runtime_model_gateway_recovery.py \
  backend/tests/test_agent_runtime_c7_bg4_runtime_gateway.py \
  backend/tests/test_agent_runtime_model_adapter.py \
  backend/tests/test_agent_runtime_model_adapter_audit.py \
  backend/tests/test_agent_runtime_model_resolution.py \
  backend/tests/test_agent_runtime_process_settings.py \
  backend/tests/test_agent_runtime_production_process_contract.py \
  backend/tests/test_configuration_bundles.py \
  backend/tests/test_configuration_envelope.py \
  backend/tests/test_configuration_resolver.py

if [ "$mode" = all ]; then
  RUN_AR17_1_DB_TEST=1 "$python_bin" -m pytest -q -p no:cacheprovider -m external \
    backend/tests/test_agent_runtime_model_gateway_postgres_external.py \
    backend/tests/test_agent_runtime_model_gateway_predispatch_postgres_external.py \
    backend/tests/test_agent_runtime_model_gateway_dispatch_binding_postgres_external.py \
    backend/tests/test_agent_runtime_model_gateway_e2e_postgres_external.py
fi

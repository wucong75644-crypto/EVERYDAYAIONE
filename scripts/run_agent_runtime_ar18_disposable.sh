#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python)}"
PYTEST_ARGS=(-q --tb=short -p no:warnings -p testing.pytest_policy)

run_tests() {
  local mode="$1"
  shift
  local marker_args=()
  if [[ "${mode}" == external ]]; then
    marker_args=(-m external)
  elif [[ "${mode}" != unit && "${mode}" != migration ]]; then
    echo "usage: $0 {unit|migration|external}" >&2
    exit 2
  fi
  local command=("${PYTHON_BIN}" -m pytest "${PYTEST_ARGS[@]}")
  if [[ "${#marker_args[@]}" -gt 0 ]]; then
    command+=("${marker_args[@]}")
  fi
  command+=("$@")
  "${command[@]}"
}

cd "${ROOT_DIR}"

case "${1:-}" in
  unit)
    run_tests unit \
      backend/tests/test_agent_runtime_ar18_ci_contract.py \
      backend/tests/test_agent_runtime_task_cancel_intent_migration.py \
      backend/tests/test_agent_runtime_task_cancel_facade_v2_migration.py \
      backend/tests/test_agent_runtime_web_task_cancel.py \
      backend/tests/test_agent_runtime_web_task_cancel_aggregation.py \
      backend/tests/test_agent_runtime_ar18_b3_action_loop.py \
      backend/tests/test_agent_runtime_ar18_b3_provider_cancel_migration.py \
      backend/tests/test_agent_runtime_ar18_b4_model_cancel_migration.py \
      backend/tests/test_agent_runtime_ar18_b5_action_loop.py \
      backend/tests/test_agent_runtime_ar18_b5_sandbox_cancel_migration.py \
      backend/tests/test_agent_runtime_ar18_b5_sandbox_worker.py \
      backend/tests/test_agent_runtime_ar18_b6_child_cancel.py \
      backend/tests/test_agent_runtime_ar18_b6_child_cancel_migration.py \
      backend/tests/test_agent_runtime_ar18_b7_scheduler_control.py \
      backend/tests/test_agent_runtime_ar18_b7_scheduler_control_migration.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_a2_submission.py \
      backend/tests/test_agent_runtime_scheduled_finalizer.py \
      backend/tests/test_agent_runtime_scheduled_finalization_repository.py \
      backend/tests/test_agent_runtime_scheduled_wecom_worker.py \
      backend/tests/test_agent_runtime_scheduled_wecom_worker_reconcile_priority.py \
      backend/tests/test_agent_runtime_scheduled_wecom_reconcile_service.py \
      backend/tests/test_agent_runtime_scheduled_wecom_router.py \
      backend/tests/test_agent_runtime_scheduled_wecom_prepared_router.py \
      backend/tests/test_agent_runtime_model_attempt_domain.py \
      backend/tests/test_agent_runtime_model_attempt_migration.py \
      backend/tests/test_agent_runtime_model_attempt_migration_order.py \
      backend/tests/test_agent_runtime_model_attempt_repository.py
    ;;
  migration)
    run_tests migration \
      backend/tests/test_agent_runtime_task_cancel_intent_migration.py \
      backend/tests/test_agent_runtime_task_cancel_facade_v2_migration.py \
      backend/tests/test_agent_runtime_ar18_b3_provider_cancel_migration.py \
      backend/tests/test_agent_runtime_ar18_b4_model_cancel_migration.py \
      backend/tests/test_agent_runtime_ar18_b5_sandbox_cancel_migration.py \
      backend/tests/test_agent_runtime_ar18_b6_child_cancel_migration.py \
      backend/tests/test_agent_runtime_ar18_b7_scheduler_control_migration.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_a2_submission_migration.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_migration.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1b_finalizer_migration.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1c_budget_migration.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1d1_delivery_migration.py \
      backend/tests/test_agent_runtime_scheduled_wecom_reconcile_org_migration.py \
      backend/tests/test_agent_runtime_ar18_migration_continuity.py \
      backend/tests/test_agent_runtime_scheduled_adoption.py \
      backend/tests/test_agent_runtime_scheduled_adoption_contract_migration.py \
      backend/tests/test_agent_runtime_scheduled_owner_convergence_migration.py \
      backend/tests/test_agent_runtime_model_attempt_migration.py \
      backend/tests/test_agent_runtime_model_attempt_migration_order.py
    ;;
  external)
    run_tests external \
      backend/tests/test_agent_runtime_task_cancel_intent_postgres_external.py \
      backend/tests/test_agent_runtime_task_cancel_facade_v2_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b3_provider_cancel_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b4_model_cancel_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b5_sandbox_cancel_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b6_child_cancel_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b6_child_cancel_fix_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_scheduler_control_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_scheduler_control_p1_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_a1_toolset_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_a2_submission_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1c_budget_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1d1_delivery_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1d1b_projection_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_claim_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_reconcile_claim_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_continuation_claim_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_reconcile_still_unknown_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_reconcile_definitive_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_dispatch_version_readback_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_dispatch_version_readback_races_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_dispatch_payload_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_unsupported_terminalization_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_started_recovery_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_unicode_payload_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_configuration_facade_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_prepared_payload_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_app_receipt_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_smart_receipt_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_prepared_recovery_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_terminal_fence_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_wecom_reconcile_org_postgres_external.py \
      backend/tests/test_agent_runtime_model_attempt_postgres_external.py \
      backend/tests/test_agent_runtime_model_attempt_permissions_postgres_external.py \
      backend/tests/test_agent_runtime_model_attempt_credits_postgres_external.py \
      backend/tests/test_agent_runtime_ar18_migration_continuity_postgres_external.py \
      backend/tests/test_agent_runtime_scheduled_adoption_postgres_external.py \
      backend/tests/test_agent_runtime_wecom_owner_closure_postgres_external.py
    ;;
  *)
    echo "usage: $0 {unit|migration|external}" >&2
    exit 2
    ;;
esac

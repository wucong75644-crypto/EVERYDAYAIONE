#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python)}"
PYTEST_ARGS=(-q --tb=short -p no:warnings -p testing.pytest_policy)

MEDIA_MIGRATION_FILES=(
  backend/migrations/228_01_agent_runtime_action_hash_canonicalization.sql
  backend/migrations/228_02_agent_runtime_batch_media_release.sql
  backend/migrations/228_03_agent_runtime_media_authorization_group.sql
  backend/migrations/228_04_agent_runtime_media_action_bindings.sql
  backend/migrations/228_05_agent_runtime_media_manifest_readback.sql
  backend/migrations/228_06_agent_runtime_media_projection.sql
  backend/migrations/228_06a_agent_runtime_media_projection_isolation.sql
  backend/migrations/228_06b_agent_runtime_media_projection_readiness.sql
  backend/migrations/228_06c_agent_runtime_media_slot_release.sql
  backend/migrations/228_07_agent_runtime_media_controls.sql
  backend/migrations/228_08a_agent_runtime_media_model_video.sql
  backend/migrations/228_08b_agent_runtime_media_wecom_delivery.sql
  backend/migrations/228_08c_agent_runtime_media_worker_scope.sql
  backend/migrations/228_08d_agent_runtime_media_atomic_image_batch.sql
  backend/migrations/228_08e1_agent_runtime_media_model_video_fence.sql
  backend/migrations/228_08e2_agent_runtime_media_model_video_projection.sql
  backend/migrations/228_08f1_agent_runtime_media_prepared_image_batch_projection.sql
  backend/migrations/228_08f2_agent_runtime_media_atomic_image_batch_ownership.sql
  backend/migrations/228_08g1_agent_runtime_media_real_event_normalization.sql
  backend/migrations/228_08g2_agent_runtime_media_model_video_wecom_outbox.sql
  backend/migrations/rollback/228_01_agent_runtime_action_hash_canonicalization_rollback.sql
  backend/migrations/rollback/228_02_agent_runtime_batch_media_release_rollback.sql
  backend/migrations/rollback/228_03_agent_runtime_media_authorization_group_rollback.sql
  backend/migrations/rollback/228_04_agent_runtime_media_action_bindings_rollback.sql
  backend/migrations/rollback/228_05_agent_runtime_media_manifest_readback_rollback.sql
  backend/migrations/rollback/228_06_agent_runtime_media_projection_rollback.sql
  backend/migrations/rollback/228_06a_agent_runtime_media_projection_isolation_rollback.sql
  backend/migrations/rollback/228_06b_agent_runtime_media_projection_readiness_rollback.sql
  backend/migrations/rollback/228_06c_agent_runtime_media_slot_release_rollback.sql
  backend/migrations/rollback/228_07_agent_runtime_media_controls_rollback.sql
  backend/migrations/rollback/228_08a_agent_runtime_media_model_video_rollback.sql
  backend/migrations/rollback/228_08b_agent_runtime_media_wecom_delivery_rollback.sql
  backend/migrations/rollback/228_08c_agent_runtime_media_worker_scope_rollback.sql
  backend/migrations/rollback/228_08d_agent_runtime_media_atomic_image_batch_rollback.sql
  backend/migrations/rollback/228_08e1_agent_runtime_media_model_video_fence_rollback.sql
  backend/migrations/rollback/228_08e2_agent_runtime_media_model_video_projection_rollback.sql
  backend/migrations/rollback/228_08f1_agent_runtime_media_prepared_image_batch_projection_rollback.sql
  backend/migrations/rollback/228_08f2_agent_runtime_media_atomic_image_batch_ownership_rollback.sql
  backend/migrations/rollback/228_08g1_agent_runtime_media_real_event_normalization_rollback.sql
  backend/migrations/rollback/228_08g2_agent_runtime_media_model_video_wecom_outbox_rollback.sql
)

MEDIA_UNIT_TESTS=(
  backend/tests/test_agent_runtime_batch_media_model.py
  backend/tests/test_agent_runtime_batch_media_release.py
  backend/tests/test_agent_runtime_chat_media_owner.py
  backend/tests/test_agent_runtime_kie_media_provider.py
  backend/tests/test_agent_runtime_media_completion.py
  backend/tests/test_agent_runtime_media_controls_composition_contract.py
  backend/tests/test_agent_runtime_media_ingress.py
  backend/tests/test_agent_runtime_media_production_composition.py
  backend/tests/test_agent_runtime_media_projection_worker.py
  backend/tests/test_agent_runtime_media_safe_download.py
  backend/tests/test_agent_runtime_media_task_port.py
  backend/tests/test_background_task_worker_media_scope.py
  backend/tests/test_file_upload_runtime_media.py
  backend/tests/test_image_runtime_request_guardrails.py
  backend/tests/test_media_tool_executor.py
  backend/tests/test_message_image_preparation.py
  backend/tests/test_message_video_preparation.py
  backend/tests/test_runtime_media_message_control.py
  backend/tests/test_worker_media_failure_settlement.py
  backend/tests/test_worker_media_task_control.py
)

MEDIA_MIGRATION_TESTS=(
  backend/tests/test_agent_runtime_action_hash_canonicalization_migration.py
  backend/tests/test_agent_runtime_batch_media_release.py
  backend/tests/test_agent_runtime_media_authorization_group_migration.py
  backend/tests/test_agent_runtime_media_action_bindings_migration.py
  backend/tests/test_agent_runtime_media_manifest_readback_migration.py
  backend/tests/test_agent_runtime_media_projection_migration.py
  backend/tests/test_agent_runtime_media_controls_migration.py
  backend/tests/test_agent_runtime_media_controls_composition_contract.py
  backend/tests/test_agent_runtime_media_model_video_migration.py
  backend/tests/test_agent_runtime_media_wecom_delivery_migration.py
  backend/tests/test_agent_runtime_media_worker_scope_migration.py
  backend/tests/test_agent_runtime_media_atomic_image_batch_migration.py
  backend/tests/test_agent_runtime_media_model_video_projection_fence_migration.py
  backend/tests/test_agent_runtime_media_prepared_image_batch_projection_migration.py
  backend/tests/test_agent_runtime_media_atomic_image_batch_ownership_migration.py
  backend/tests/test_agent_runtime_media_real_event_normalization_migration.py
)

MEDIA_EXTERNAL_TESTS=(
  backend/tests/test_agent_runtime_action_hash_canonicalization_postgres_external.py
  backend/tests/test_agent_runtime_batch_media_release.py
  backend/tests/test_agent_runtime_media_authorization_group_postgres_external.py
  backend/tests/test_agent_runtime_media_action_bindings_postgres_external.py
  backend/tests/test_agent_runtime_media_action_binding_serial_postgres_external.py
  backend/tests/test_agent_runtime_media_manifest_readback_postgres_external.py
  backend/tests/test_agent_runtime_media_ecom_postgres_external.py
  backend/tests/test_agent_runtime_media_projection_postgres_external.py
  backend/tests/test_agent_runtime_media_projection_controls_postgres_external.py
  backend/tests/test_agent_runtime_media_projection_review_postgres_external.py
  backend/tests/test_agent_runtime_media_slot_release_postgres_external.py
  backend/tests/test_agent_runtime_media_controls_postgres_external.py
  backend/tests/test_agent_runtime_media_controls_concurrency_postgres_external.py
  backend/tests/test_agent_runtime_media_controls_composition_postgres_external.py
  backend/tests/test_agent_runtime_media_formal_composition_postgres_external.py
  backend/tests/test_agent_runtime_media_model_video_postgres_external.py
  backend/tests/test_agent_runtime_media_wecom_delivery_postgres_external.py
  backend/tests/test_agent_runtime_media_worker_scope_postgres_external.py
  backend/tests/test_agent_runtime_media_atomic_image_batch_postgres_external.py
  backend/tests/test_agent_runtime_media_model_video_projection_fence_postgres_external.py
  backend/tests/test_agent_runtime_media_prepared_image_batch_projection_postgres_external.py
  backend/tests/test_agent_runtime_media_atomic_image_batch_ownership_postgres_external.py
  backend/tests/test_agent_runtime_media_real_event_normalization_postgres_external.py
  backend/tests/test_agent_runtime_media_real_event_terminal_postgres_external.py
)

require_files() {
  local path
  for path in "$@"; do
    if [[ ! -f "${path}" ]]; then
      echo "required Runtime media CI dependency is missing: ${path}" >&2
      exit 3
    fi
  done
}

require_media_batch_closure() {
  require_files \
    "${MEDIA_MIGRATION_FILES[@]}" \
    "${MEDIA_UNIT_TESTS[@]}" \
    "${MEDIA_MIGRATION_TESTS[@]}" \
    "${MEDIA_EXTERNAL_TESTS[@]}"
}

_MODEL_ATTEMPT_PG_DIR=""

cleanup_model_attempt_postgres() {
  if [[ -z "${_MODEL_ATTEMPT_PG_DIR}" ]]; then
    return
  fi
  local pg_ctl_path="${_MODEL_ATTEMPT_PG_DIR}/pg_ctl"
  if [[ -x "${pg_ctl_path}" ]]; then
    "${pg_ctl_path}" -D "${_MODEL_ATTEMPT_PG_DIR}/data" -m immediate -w stop >/dev/null 2>&1 || true
  fi
  if [[ "$(basename "${_MODEL_ATTEMPT_PG_DIR}")" == runtime-ar11-pg.* ]]; then
    rm -rf -- "${_MODEL_ATTEMPT_PG_DIR}"
  fi
  _MODEL_ATTEMPT_PG_DIR=""
}

start_model_attempt_postgres() {
  local pg_bin_dir="${AGENT_RUNTIME_PG_BIN_DIR:-}"
  if [[ -z "${pg_bin_dir}" ]] && command -v initdb >/dev/null 2>&1; then
    pg_bin_dir="$(dirname "$(command -v initdb)")"
  fi
  if [[ -z "${pg_bin_dir}" ]] && [[ -x /opt/homebrew/bin/initdb ]]; then
    pg_bin_dir=/opt/homebrew/bin
  fi
  for command_name in initdb pg_ctl createdb; do
    if [[ ! -x "${pg_bin_dir}/${command_name}" ]]; then
      echo "required disposable PostgreSQL command is missing: ${command_name}" >&2
      exit 4
    fi
  done

  _MODEL_ATTEMPT_PG_DIR="$(mktemp -d "${TMPDIR:-/private/tmp}/runtime-ar11-pg.XXXXXX")"
  ln -s "${pg_bin_dir}/pg_ctl" "${_MODEL_ATTEMPT_PG_DIR}/pg_ctl"
  local port
  port="$(${PYTHON_BIN} -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  "${pg_bin_dir}/initdb" -D "${_MODEL_ATTEMPT_PG_DIR}/data" -U postgres \
    --auth-host=trust --auth-local=trust >/dev/null
  "${pg_bin_dir}/pg_ctl" -D "${_MODEL_ATTEMPT_PG_DIR}/data" \
    -l "${_MODEL_ATTEMPT_PG_DIR}/postgres.log" \
    -o "-h 127.0.0.1 -p ${port} -F" -w start >/dev/null
  "${pg_bin_dir}/createdb" -h 127.0.0.1 -p "${port}" -U postgres ar11_runtime_ci
  export RUN_AR11_DB_TEST=1
  export AR11_TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:${port}/ar11_runtime_ci"
  trap cleanup_model_attempt_postgres EXIT
}

run_tests() {
  local mode="$1"
  shift
  local marker_args=()
  if [[ "${mode}" == external ]]; then
    marker_args=(-m external)
  elif [[ "${mode}" == unit || "${mode}" == migration ]]; then
    marker_args=(-m "not external")
  else
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
require_media_batch_closure

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
      backend/tests/test_agent_runtime_model_attempt_repository.py \
      "${MEDIA_UNIT_TESTS[@]}"
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
      backend/tests/test_agent_runtime_model_attempt_migration_order.py \
      "${MEDIA_MIGRATION_TESTS[@]}"
    ;;
  external)
    start_model_attempt_postgres
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
      backend/tests/test_agent_runtime_wecom_owner_closure_postgres_external.py \
      "${MEDIA_EXTERNAL_TESTS[@]}"
    ;;
  *)
    echo "usage: $0 {unit|migration|external}" >&2
    exit 2
    ;;
esac

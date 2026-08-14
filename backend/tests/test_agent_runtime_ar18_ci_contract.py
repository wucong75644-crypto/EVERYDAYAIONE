"""Static contract for the flags-off AR-18 disposable verification lane."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT.parent / ".github/workflows/agent-runtime-disposable.yml").read_text(
    encoding="utf-8"
)
RUNNER = (ROOT.parent / "scripts/run_agent_runtime_ar18_disposable.sh").read_text(
    encoding="utf-8"
)


def test_ar18_runner_is_the_ci_entrypoint_and_keeps_flags_off() -> None:
    assert "scripts/run_agent_runtime_ar18_disposable.sh unit" in WORKFLOW
    assert "scripts/run_agent_runtime_ar18_disposable.sh migration" in WORKFLOW
    assert "scripts/run_agent_runtime_ar18_disposable.sh external" in WORKFLOW
    assert 'AGENT_RUNTIME_INGRESS_ENABLED: "false"' in WORKFLOW
    assert 'AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED: "false"' in WORKFLOW
    assert 'AGENT_RUNTIME_SCHEDULED_WECOM_ENABLED: "false"' in WORKFLOW
    assert 'AGENT_RUNTIME_MEDIA_ENABLED: "false"' in WORKFLOW
    assert 'AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED: "false"' in WORKFLOW
    assert 'AGENT_RUNTIME_MEDIA_PRODUCTION_READY: "false"' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_INGRESS_ENABLED" = false' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED" = false' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_SCHEDULED_WECOM_ENABLED" = false' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_MEDIA_ENABLED" = false' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED" = false' in WORKFLOW
    assert 'test "$AGENT_RUNTIME_MEDIA_PRODUCTION_READY" = false' in WORKFLOW
    assert 'test -z "${KIE_API_KEY:-}"' in WORKFLOW
    assert "production_ready=false" in WORKFLOW


def test_runtime_media_changes_trigger_the_disposable_lane() -> None:
    required_paths = (
        "backend/migrations/217_*.sql",
        "backend/migrations/rollback/217_*.sql",
        "backend/migrations/228_*.sql",
        "backend/migrations/rollback/228_*.sql",
        "backend/api/routes/message_image_preparation.py",
        "backend/api/routes/message_video_preparation.py",
        "backend/services/background_task_worker.py",
        "backend/services/async_retry_service.py",
        "backend/services/task_completion_service.py",
        "backend/services/agent/tool_executor.py",
        "backend/services/media_tool_executor.py",
        "frontend/src/components/chat/media/AiImageGrid.tsx",
        "frontend/src/components/chat/message/MessageMedia.tsx",
        "frontend/src/contexts/wsTaskMessageHandlers.ts",
        "frontend/src/schemas/messageProtocol.ts",
        "frontend/src/services/messageSender.ts",
        "frontend/src/utils/runtimeMediaSlots.ts",
    )
    for path in required_paths:
        assert f'- "{path}"' in WORKFLOW


def test_runtime_media_runner_fails_closed_when_composition_is_incomplete() -> None:
    assert "require_media_batch_closure" in RUNNER
    assert "required Runtime media CI dependency is missing" in RUNNER
    for sequence in range(1, 8):
        prefix = f"228_{sequence:02d}_agent_runtime_"
        assert f"backend/migrations/{prefix}" in RUNNER
        assert f"backend/migrations/rollback/{prefix}" in RUNNER
    for identity in (
        "228_06a_agent_runtime_media_projection_isolation",
        "228_06b_agent_runtime_media_projection_readiness",
        "228_06c_agent_runtime_media_slot_release",
        "228_08a_agent_runtime_media_model_video",
        "228_08b_agent_runtime_media_wecom_delivery",
        "228_08c_agent_runtime_media_worker_scope",
        "228_08d_agent_runtime_media_atomic_image_batch",
        "228_08e1_agent_runtime_media_model_video_fence",
        "228_08e2_agent_runtime_media_model_video_projection",
        "228_08f1_agent_runtime_media_prepared_image_batch_projection",
        "228_08f2_agent_runtime_media_atomic_image_batch_ownership",
        "228_08g1_agent_runtime_media_real_event_normalization",
        "228_08g2_agent_runtime_media_model_video_wecom_outbox",
        "228_08h_agent_runtime_scheduled_web_projection_claim_ordering",
        "228_08i1_agent_runtime_media_real_image_event_normalization",
        "228_08i2_agent_runtime_media_model_image_wecom_outbox",
    ):
        assert f"backend/migrations/{identity}.sql" in RUNNER
        assert f"backend/migrations/rollback/{identity}_rollback.sql" in RUNNER

    for dependency in (
        "test_agent_runtime_media_manifest_readback_migration.py",
        "test_agent_runtime_media_projection_migration.py",
        "test_agent_runtime_media_controls_migration.py",
        "test_agent_runtime_media_controls_composition_contract.py",
        "test_agent_runtime_media_controls_composition_postgres_external.py",
        "test_agent_runtime_media_model_video_migration.py",
        "test_agent_runtime_media_model_video_postgres_external.py",
        "test_agent_runtime_media_wecom_delivery_migration.py",
        "test_agent_runtime_media_wecom_delivery_postgres_external.py",
        "test_agent_runtime_media_worker_scope_migration.py",
        "test_agent_runtime_media_worker_scope_postgres_external.py",
        "test_agent_runtime_media_atomic_image_batch_migration.py",
        "test_agent_runtime_media_atomic_image_batch_postgres_external.py",
        "test_agent_runtime_media_model_video_projection_fence_migration.py",
        "test_agent_runtime_media_model_video_projection_fence_postgres_external.py",
        "test_agent_runtime_media_prepared_image_batch_projection_migration.py",
        "test_agent_runtime_media_prepared_image_batch_projection_postgres_external.py",
        "test_agent_runtime_media_atomic_image_batch_ownership_migration.py",
        "test_agent_runtime_media_atomic_image_batch_ownership_postgres_external.py",
        "test_background_task_worker_media_scope.py",
        "test_image_runtime_request_guardrails.py",
        "test_agent_runtime_media_real_event_normalization_migration.py",
        "test_agent_runtime_media_real_event_normalization_postgres_external.py",
        "test_agent_runtime_media_real_event_terminal_postgres_external.py",
        "test_agent_runtime_scheduled_web_projection_claim_ordering_migration.py",
        "test_agent_runtime_scheduled_web_projection_claim_ordering_postgres_external.py",
        "test_agent_runtime_media_real_image_events_migration.py",
        "test_agent_runtime_media_real_image_events_postgres_external.py",
        "test_agent_runtime_media_model_image_wecom_postgres_external.py",
        "test_agent_runtime_media_candidate_rollback_guards_postgres_external.py",
        "test_agent_runtime_media_full_chain_rollback_postgres_external.py",
    ):
        assert dependency in RUNNER


def test_runtime_media_unit_migration_and_external_contracts_are_wired() -> None:
    required = (
        "test_agent_runtime_batch_media_model.py",
        "test_agent_runtime_batch_media_release.py",
        "test_agent_runtime_media_authorization_group_postgres_external.py",
        "test_agent_runtime_media_action_bindings_postgres_external.py",
        "test_agent_runtime_kie_media_provider.py",
        "test_agent_runtime_media_projection_worker.py",
        "test_agent_runtime_media_safe_download.py",
        "test_agent_runtime_media_projection_controls_postgres_external.py",
        "test_agent_runtime_media_projection_review_postgres_external.py",
        "test_agent_runtime_media_slot_release_postgres_external.py",
        "test_agent_runtime_media_controls_postgres_external.py",
        "test_agent_runtime_media_controls_concurrency_postgres_external.py",
        "test_agent_runtime_media_formal_composition_postgres_external.py",
        "test_agent_runtime_media_ecom_postgres_external.py",
        "test_agent_runtime_chat_media_owner.py",
        "test_media_tool_executor.py",
        "test_message_image_preparation.py",
        "test_message_video_preparation.py",
        "test_worker_media_failure_settlement.py",
        "test_worker_media_task_control.py",
    )
    for test_name in required:
        assert test_name in RUNNER
    assert 'marker_args=(-m "not external")' in RUNNER
    assert "marker_args=(-m external)" in RUNNER


def test_runtime_media_frontend_contracts_and_build_are_wired() -> None:
    for test_name in (
        "AiImageGrid.test.tsx",
        "MessageMedia.test.tsx",
        "RuntimeMediaMessageItem.test.tsx",
        "wsRuntimeMediaSlots.test.ts",
        "messageProtocol.test.ts",
        "runtimeMediaSlots.test.ts",
        "messageSenderRetry.test.ts",
        "models.test.ts",
    ):
        assert test_name in WORKFLOW
    assert "npm run test:run --" in WORKFLOW
    assert "npm run build" in WORKFLOW


def test_ar18_runner_contains_each_required_lane() -> None:
    required = (
        "test_agent_runtime_task_cancel_intent_postgres_external.py",
        "test_agent_runtime_task_cancel_facade_v2_postgres_external.py",
        "test_agent_runtime_web_task_cancel.py",
        "test_agent_runtime_ar18_b3_provider_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b4_model_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b5_sandbox_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b6_child_cancel_postgres_external.py",
        "test_agent_runtime_ar18_b7_scheduler_control_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1c_budget_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1d1_delivery_postgres_external.py",
        "test_agent_runtime_ar18_b7_s2_b1d1b_projection_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_claim_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_still_unknown_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_terminal_fence_postgres_external.py",
        "test_agent_runtime_model_attempt_postgres_external.py",
        "test_agent_runtime_model_attempt_permissions_postgres_external.py",
        "test_agent_runtime_model_attempt_credits_postgres_external.py",
        "test_agent_runtime_ar18_migration_continuity.py",
        "test_agent_runtime_ar18_migration_continuity_postgres_external.py",
    )
    for test_name in required:
        assert test_name in RUNNER


def test_projection_rollback_closure_is_ordered_and_triggered() -> None:
    migration_g2 = (
        "backend/migrations/228_08g2_agent_runtime_media_model_video_wecom_outbox.sql"
    )
    migration_h = (
        "backend/migrations/228_08h_agent_runtime_scheduled_web_projection_"
        "claim_ordering.sql"
    )
    rollback_g2 = (
        "backend/migrations/rollback/"
        "228_08g2_agent_runtime_media_model_video_wecom_outbox_rollback.sql"
    )
    rollback_h = (
        "backend/migrations/rollback/"
        "228_08h_agent_runtime_scheduled_web_projection_claim_ordering_rollback.sql"
    )
    migration_i1 = (
        "backend/migrations/228_08i1_agent_runtime_media_real_image_event_"
        "normalization.sql"
    )
    migration_i2 = (
        "backend/migrations/228_08i2_agent_runtime_media_model_image_wecom_outbox.sql"
    )
    rollback_i1 = (
        "backend/migrations/rollback/"
        "228_08i1_agent_runtime_media_real_image_event_normalization_rollback.sql"
    )
    rollback_i2 = (
        "backend/migrations/rollback/"
        "228_08i2_agent_runtime_media_model_image_wecom_outbox_rollback.sql"
    )
    assert RUNNER.index(migration_g2) < RUNNER.index(migration_h)
    assert RUNNER.index(migration_h) < RUNNER.index(migration_i1)
    assert RUNNER.index(migration_i1) < RUNNER.index(migration_i2)
    assert RUNNER.index(rollback_g2) < RUNNER.index(rollback_h)
    assert RUNNER.index(rollback_h) < RUNNER.index(rollback_i1)
    assert RUNNER.index(rollback_i1) < RUNNER.index(rollback_i2)

    for test_name in (
        "test_agent_runtime_scheduled_web_projection_claim_ordering_migration.py",
        "test_agent_runtime_scheduled_web_projection_claim_ordering_postgres_external.py",
        "test_agent_runtime_media_real_image_events_migration.py",
        "test_agent_runtime_media_real_image_events_postgres_external.py",
        "test_agent_runtime_media_model_image_wecom_postgres_external.py",
        "test_agent_runtime_media_candidate_rollback_guards_postgres_external.py",
        "test_agent_runtime_media_full_chain_rollback_postgres_external.py",
    ):
        assert RUNNER.count(test_name) == 1

    assert RUNNER.count(
        "test_agent_runtime_ar18_b7_s2_b1d1b_projection_postgres_external.py"
    ) == 1
    for trigger_path in (
        'backend/migrations/228_*.sql',
        'backend/migrations/rollback/228_*.sql',
        'backend/tests/test_agent_runtime_*.py',
        'scripts/run_agent_runtime_ar18_disposable.sh',
    ):
        assert f'- "{trigger_path}"' in WORKFLOW


def test_workflow_runs_the_scheduled_wecom_disposable_lane() -> None:
    assert "Run Scheduled WeCom disposable PostgreSQL contracts" in WORKFLOW
    assert "test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external.py" in WORKFLOW
    assert "test_agent_runtime_scheduled_wecom_reconcile_definitive_postgres_external.py" in WORKFLOW
    assert "test_agent_runtime_scheduled_wecom_prepared_recovery_postgres_external.py" in WORKFLOW


def test_model_attempt_lane_explicitly_covers_unknown_and_reconcile() -> None:
    assert "start_model_attempt_postgres" in RUNNER
    assert "RUN_AR11_DB_TEST=1" in RUNNER
    assert "AR11_TEST_DATABASE_URL=" in RUNNER
    assert "required disposable PostgreSQL command is missing" in RUNNER
    assert "test_unknown_is_non_terminal_and_fencing_fails_closed" in (
        (ROOT / "tests/test_agent_runtime_model_attempt_postgres_external.py").read_text(
            encoding="utf-8"
        )
    )
    assert "test_reconcile_completed_is_atomic" in (
        (ROOT / "tests/test_agent_runtime_model_attempt_permissions_postgres_external.py").read_text(
            encoding="utf-8"
        )
    )


def test_workflow_keeps_scheduled_wecom_and_continuity_lanes() -> None:
    for test_name in (
        "test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_definitive_postgres_external.py",
        "test_agent_runtime_scheduled_wecom_reconcile_org_migration.py",
        "test_agent_runtime_ar18_migration_continuity_postgres_external.py",
    ):
        assert test_name in RUNNER

"""Operational safety contract for the migration 161 runbook."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = (
    ROOT / "docs/document/RUNBOOK_161_旧配置迁移.md"
).read_text(encoding="utf-8")
TEMPLATE = (
    ROOT / "deploy/env-templates/legacy-config-import.env.template"
).read_text(encoding="utf-8")


def test_runbook_orders_preflight_dry_run_apply_and_verify() -> None:
    stages = (
        "## 5. 数据库只读 preflight",
        "## 7. 执行 dry-run",
        "## 8. 双人确认",
        "## 9. 原子 apply",
        "## 10. 导入后只读验证",
    )
    positions = [RUNBOOK.index(stage) for stage in stages]

    assert positions == sorted(positions)


def test_runbook_uses_fixed_import_id_and_exact_confirmation() -> None:
    assert "--import-id \"$IMPORT_ID\"" in RUNBOOK
    assert '--confirm "APPLY:$IMPORT_ID"' in RUNBOOK
    assert "dry-run 与 apply 必须使用同一个 `IMPORT_ID`" in RUNBOOK


def test_runbook_preserves_legacy_truth_and_durable_audit() -> None:
    for rule in (
        "不删除或修改旧配置",
        "不删除 `configuration_import_audit_log`",
        "不手工修改 `schema_migration_ledger`",
        "成功导入后不直接执行 161 rollback",
        "保持消费者读取旧配置",
    ):
        assert rule in RUNBOOK


def test_post_import_checks_audit_entries_secrets_and_single_batch() -> None:
    for contract in (
        "configuration_import_audit_log",
        "matched_entry_count",
        "active_v1_count",
        "invalid_secret_count",
        "import_batch_count=1",
    ):
        assert contract in RUNBOOK


def test_runbook_requires_reader_migrator_separation_and_no_systemd() -> None:
    assert "everydayai_config_import_reader" in RUNBOOK
    assert "everydayai_migrator" in RUNBOOK
    assert "两个 URL 不相同" in RUNBOOK
    assert "不得加入任何 Systemd `EnvironmentFile`" in RUNBOOK


def test_one_shot_template_is_shell_safe_and_contains_no_real_secret() -> None:
    assert (
        "everydayai_config_import_reader:<reader-password>" in TEMPLATE
    )
    assert (
        "everydayai_migrator:<migrator-password>" in TEMPLATE
    )
    assert "CONFIG_KEK_KEYRING_JSON='{" in TEMPLATE
    assert "<legacy-global-fallback-key>" in TEMPLATE

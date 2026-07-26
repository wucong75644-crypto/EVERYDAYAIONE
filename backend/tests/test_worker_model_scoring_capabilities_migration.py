"""Worker 模型评分窄能力迁移合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT / "backend/migrations/198_worker_model_scoring_capabilities.sql"
)
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/"
    "198_worker_model_scoring_capabilities_rollback.sql"
)


def test_snapshot_is_worker_scoped_and_personal_metrics_are_grouped() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "session_user <> 'everydayai_worker'" in sql
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in sql
    assert "CASE WHEN p_org_id IS NULL THEN metric.user_id END" in sql
    assert "audit.owner_user_id IS NOT DISTINCT FROM" in sql
    assert "SECURITY DEFINER" in sql


def test_commit_is_atomic_and_has_no_worker_table_grants() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    commit = sql.split("CREATE FUNCTION worker_commit_model_score", 1)[1]
    assert "INSERT INTO knowledge_nodes" in commit
    assert "INSERT INTO scoring_audit_log" in commit
    assert "MODEL_SCORING_OWNER_SCOPE_MISMATCH" in commit
    assert "MODEL_SCORING_ARGUMENT_INVALID" in commit
    assert "MODEL_SCORING_KNOWLEDGE_INVALID" in commit
    assert "pg_advisory_xact_lock" in commit
    assert commit.index("pg_advisory_xact_lock") < commit.index(
        "SELECT node.id INTO v_node_id"
    )
    assert "GRANT SELECT" not in sql
    assert "GRANT INSERT" not in sql
    assert "TO everydayai_worker;" in sql


def test_rollback_refuses_to_drop_personal_audit_ownership() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    guard = "SCORING_PERSONAL_FACTS_REQUIRE_FORWARD_ROLLBACK"
    assert guard in sql
    assert sql.index(guard) < sql.index("DROP COLUMN owner_user_id")

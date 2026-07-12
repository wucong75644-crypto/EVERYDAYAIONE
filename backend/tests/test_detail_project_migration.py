"""主图详情项目迁移契约测试。"""

from pathlib import Path


MIGRATION = Path(__file__).parent.parent / "migrations" / "118_detail_projects.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_defines_normalized_tables_and_constraints() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS detail_projects" in sql
    assert "CREATE TABLE IF NOT EXISTS detail_project_images" in sql
    assert "UNIQUE (project_id, workspace_path)" in sql
    assert "UNIQUE (project_id, sort_order)" in sql
    assert "image_count BETWEEN 1 AND 9" in sql


def test_migration_has_personal_and_org_draft_uniqueness() -> None:
    sql = _sql()
    assert "uq_detail_projects_org_draft" in sql
    assert "status = 'draft' AND org_id IS NOT NULL" in sql
    assert "uq_detail_projects_personal_draft" in sql
    assert "status = 'draft' AND org_id IS NULL" in sql


def test_attach_rpc_serializes_and_enforces_shared_limit() -> None:
    sql = _sql()
    assert "CREATE OR REPLACE FUNCTION attach_detail_project_image" in sql
    assert "FOR UPDATE" in sql
    assert "org_id IS NOT DISTINCT FROM p_org_id" in sql
    assert "IF v_count >= 9" in sql
    assert "DETAIL_IMAGE_LIMIT_EXCEEDED" in sql
    assert "DETAIL_IMAGE_DUPLICATE" in sql
    assert "image.project_id = v_project_id" in sql


def test_migration_uses_local_db_access_model_and_documents_rollback() -> None:
    sql = _sql()
    assert "auth.uid()" not in sql
    assert "ENABLE ROW LEVEL SECURITY" not in sql
    assert "DETAIL_PROJECT_USER_NOT_FOUND" in sql
    assert "DETAIL_PROJECT_ORG_ACCESS_DENIED" in sql
    assert "member.status = 'active'" in sql
    assert "SECURITY INVOKER" in sql
    assert "REVOKE ALL ON FUNCTION attach_detail_project_image" in sql
    assert "DROP FUNCTION IF EXISTS attach_detail_project_image" in sql

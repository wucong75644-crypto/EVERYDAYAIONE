"""Canonical 用户资产与来源关联迁移契约测试。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "145_user_assets.sql"
ROLLBACK = ROOT / "migrations" / "rollback" / "145_user_assets_rollback.sql"
ADMIN_QUERY_MIGRATION = (
    ROOT / "migrations" / "146_admin_user_assets_query.sql"
)
ADMIN_QUERY_ROLLBACK = (
    ROOT / "migrations" / "rollback"
    / "146_admin_user_assets_query_rollback.sql"
)


def _migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_asset_table_uses_stable_storage_identity() -> None:
    sql = _migration_sql()
    assert "CREATE TABLE IF NOT EXISTS user_assets" in sql
    assert "storage_scope IN ('user', 'channel')" in sql
    assert "storage_provider IN ('workspace', 'oss')" in sql
    assert "uq_user_assets_storage_identity" in sql
    assert "storage_owner_key TEXT NOT NULL CHECK" in sql
    assert "storage_owner_key ~* '^[0-9a-f]{8}-" in sql
    assert "storage_owner_key ~ '^channels/wecom/[0-9a-f]{24}$'" in sql
    assert "storage_owner_key, storage_provider, storage_key" in sql
    assert "OR p_storage_owner_key IS NULL" in sql
    assert "content_sha256 TEXT CHECK" in sql
    assert "UNIQUE (content_sha256)" not in sql


def test_source_facts_are_kept_in_separate_reference_table() -> None:
    sql = _migration_sql()
    assert "CREATE TABLE IF NOT EXISTS user_asset_refs" in sql
    assert "ref_key TEXT NOT NULL UNIQUE" in sql
    assert "asset_id UUID NOT NULL REFERENCES user_assets(id) ON DELETE CASCADE" in sql
    assert "source_type IN ('upload', 'generated')" in sql
    assert "'upload', 'task', 'message', 'image_generation', 'attachment'" in sql
    assert "source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL" in sql
    assert "source_task_id UUID REFERENCES tasks(id) ON DELETE SET NULL" in sql
    assert "source_generation_id UUID REFERENCES image_generations(id) ON DELETE SET NULL" in sql
    assert "REFERENCES conversation_attachment_refs(id) ON DELETE SET NULL" in sql


def test_admin_indexes_support_exists_filter_and_asset_cursor() -> None:
    sql = _migration_sql()
    assert "idx_user_assets_admin_cursor" in sql
    assert "ON user_assets(created_at DESC, id DESC)" in sql
    assert "idx_user_assets_admin_media_cursor" in sql
    assert "idx_user_asset_refs_admin" in sql
    assert "actor_user_id, source_type, asset_id" in sql


def test_registration_rpc_is_atomic_and_concurrency_safe() -> None:
    sql = _migration_sql()
    assert "CREATE OR REPLACE FUNCTION register_user_asset(" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public" in sql
    assert "EXCEPTION WHEN unique_violation" in sql
    assert sql.count("FOR UPDATE;") >= 4
    assert "USER_ASSET_IDENTITY_CONFLICT" in sql
    assert "USER_ASSET_REF_CONFLICT" in sql
    assert "v_ref.source_task_id <> p_source_task_id" in sql
    assert "v_ref.source_message_id <> p_source_message_id" in sql
    assert "v_ref.content_index <> p_content_index" in sql
    assert "'asset_created', (v_asset_result->>'created')::BOOLEAN" in sql
    assert "'ref_created', (v_ref_result->>'created')::BOOLEAN" in sql


def test_tables_and_rpc_are_not_exposed_to_public() -> None:
    sql = _migration_sql()
    assert "ALTER TABLE user_assets ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE user_asset_refs ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE user_assets, user_asset_refs FROM PUBLIC" in sql
    assert "REVOKE ALL ON FUNCTION register_user_asset(" in sql
    assert "REVOKE ALL ON FUNCTION _resolve_user_asset(" in sql
    assert "REVOKE ALL ON FUNCTION _bind_user_asset_ref(" in sql
    assert ") FROM PUBLIC;" in sql
    assert ") TO service_role;" in sql


def test_rollback_refuses_to_drop_non_empty_asset_facts() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "IF EXISTS (SELECT 1 FROM user_asset_refs LIMIT 1)" in sql
    assert "OR EXISTS (SELECT 1 FROM user_assets LIMIT 1)" in sql
    assert "RAISE EXCEPTION 'USER_ASSETS_NOT_EMPTY'" in sql
    assert sql.index("DROP FUNCTION IF EXISTS register_user_asset") < sql.index(
        "DROP TABLE IF EXISTS user_asset_refs"
    )
    assert sql.index("DROP TABLE IF EXISTS user_asset_refs") < sql.index(
        "DROP TABLE IF EXISTS user_assets"
    )


def test_admin_query_uses_refs_and_stable_asset_cursor() -> None:
    sql = ADMIN_QUERY_MIGRATION.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION list_admin_user_assets(" in sql
    assert "FROM user_asset_refs AS asset_ref" in sql
    assert "asset_ref.actor_user_id = p_actor_user_id" in sql
    assert "asset_ref.source_type = p_source_type" in sql
    assert "JOIN LATERAL" in sql
    assert "(asset.created_at, asset.id)" in sql
    assert "(p_cursor_created_at, p_cursor_id)" in sql
    assert "<" in sql
    assert "SECURITY DEFINER" in sql
    assert "IF EXISTS (SELECT 1 FROM pg_roles" in sql
    assert "rolname = 'service_role'" in sql
    assert ") TO service_role;" in sql


def test_admin_query_rollback_drops_only_query_rpc() -> None:
    sql = ADMIN_QUERY_ROLLBACK.read_text(encoding="utf-8")
    assert "DROP FUNCTION IF EXISTS list_admin_user_assets(" in sql
    assert "DROP TABLE" not in sql

"""迁移 168 的 WeCom runtime 只读能力契约。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/168_wecom_runtime_read_capabilities.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback"
    / "168_wecom_runtime_read_capabilities_rollback.sql"
).read_text()


def test_generation_context_is_actor_and_org_scoped() -> None:
    assert "SECURITY DEFINER" in SQL
    assert "SET search_path = pg_catalog, public" in SQL
    assert "PERFORM public._assert_wecom_message_scope(v_org, v_actor)" in SQL
    assert "p_user_id IS DISTINCT FROM v_actor" in SQL
    assert "org_id = v_org" in SQL
    assert "source = 'wecom'" in SQL


def test_display_name_requires_message_and_ingress_scope() -> None:
    assert "PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id)" in SQL
    assert "PERFORM public._assert_wecom_ingress_scope(p_org_id, p_corp_id)" in SQL
    assert "user_id = p_user_id" in SQL
    assert "org_id = p_org_id" in SQL


def test_conversation_reset_rotates_binding_atomically() -> None:
    assert "CREATE FUNCTION reset_wecom_conversation" in SQL
    assert "PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id)" in SQL
    assert "FOR UPDATE" in SQL
    assert "UPDATE public.conversation_channel_bindings" in SQL
    assert "'outcome', 'reset'" in SQL


def test_memory_commands_are_actor_scoped_and_bounded() -> None:
    assert "CREATE FUNCTION get_wecom_manual_memories" in SQL
    assert "CREATE FUNCTION clear_wecom_manual_memories" in SQL
    assert SQL.count(
        "PERFORM public._assert_wecom_message_scope(p_org_id, p_user_id)"
    ) >= 4
    assert "LIMIT 100" in SQL
    assert "AND NOT is_deleted" in SQL


def test_only_wecom_runtime_receives_capabilities() -> None:
    grant = SQL[SQL.index("GRANT EXECUTE ON FUNCTION"):SQL.index(
        "TO everydayai_wecom_runtime;"
    )]
    assert "everydayai_runtime" not in grant
    assert "everydayai_worker" not in grant
    assert "FROM PUBLIC, everydayai_runtime" in SQL


def test_rollback_removes_both_capabilities() -> None:
    assert "DROP FUNCTION get_wecom_generation_context(UUID, UUID)" in ROLLBACK
    assert (
        "DROP FUNCTION update_wecom_ingress_display_name"
        "(UUID, TEXT, TEXT, UUID, TEXT)"
    ) in ROLLBACK
    assert "DROP FUNCTION reset_wecom_conversation(UUID, UUID, UUID)" in ROLLBACK
    assert "DROP FUNCTION get_wecom_manual_memories(UUID, UUID)" in ROLLBACK
    assert "DROP FUNCTION clear_wecom_manual_memories(UUID, UUID)" in ROLLBACK

"""迁移 154 的 WeCom 消息 RPC 安全包装与回滚合同。"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/154_wecom_message_rpc_facades.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback"
    / "154_wecom_message_rpc_facades_rollback.sql"
).read_text()
PUBLIC_FUNCTIONS = {
    "resolve_wecom_conversation",
    "stage_wecom_attachment_v2",
    "enqueue_wecom_generation_turn_v2",
    "update_wecom_conversation_setting",
    "record_user_activity",
}
CORE_FUNCTIONS = {
    "_resolve_wecom_conversation_core",
    "_stage_wecom_attachment_v2_core",
    "_enqueue_wecom_generation_turn_v2_core",
    "_update_wecom_conversation_setting_core",
    "_record_user_activity_core",
}


def _created_bodies(name: str) -> list[str]:
    starts = [
        match.start()
        for match in re.finditer(rf"CREATE FUNCTION {name}\(", SQL)
    ]
    return [
        SQL[start:SQL.index("\n$$;", start) + 4]
        for start in starts
    ]


def test_scope_helper_binds_wecom_role_org_actor_and_membership() -> None:
    body = SQL[SQL.index(
        "CREATE OR REPLACE FUNCTION _assert_wecom_message_scope"
    ):SQL.index("\n$$;", SQL.index(
        "CREATE OR REPLACE FUNCTION _assert_wecom_message_scope"
    ))]
    assert "session_user <> 'everydayai_wecom_runtime'" in body
    assert "app.access_kind" in body
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in body
    assert "tenant_actor_user_id() IS DISTINCT FROM p_actor_user_id" in body
    assert "tenant_actor_is_active_member(p_org_id)" in body
    assert (
        "REVOKE ALL ON FUNCTION _assert_wecom_message_scope(UUID, UUID) "
        "FROM PUBLIC;"
    ) in SQL


def test_existing_functions_are_renamed_to_ungranted_cores() -> None:
    for name in CORE_FUNCTIONS:
        assert f"RENAME TO {name};" in SQL
        assert f"{name}(" in SQL
    assert (
        "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, "
        "everydayai_worker;"
    ) in SQL
    core_grant_section = SQL[SQL.index(
        "REVOKE ALL ON FUNCTION\n    _resolve_wecom_conversation_core"
    ):SQL.index("REVOKE ALL ON FUNCTION\n    resolve_wecom_conversation")]
    assert "GRANT EXECUTE" not in core_grant_section


def test_public_wrappers_are_definer_with_fixed_search_path() -> None:
    for name in PUBLIC_FUNCTIONS:
        bodies = _created_bodies(name)
        assert bodies
        for body in bodies:
            assert "SECURITY DEFINER" in body
            assert "SET search_path = pg_catalog, public" in body


def test_message_wrappers_assert_identity_before_core_call() -> None:
    for name in (
        "resolve_wecom_conversation",
        "stage_wecom_attachment_v2",
        "enqueue_wecom_generation_turn_v2",
        "update_wecom_conversation_setting",
    ):
        for body in _created_bodies(name):
            assert body.index("_assert_wecom_message_scope") < body.index(
                f"_{name}_core"
            )
    for body in _created_bodies("enqueue_wecom_generation_turn_v2"):
        assert "_assert_wecom_ingress_scope" in body
        assert "p_delivery_context->>'corp_id'" in body


def test_wrappers_preserve_legacy_role_until_cutover() -> None:
    for name in PUBLIC_FUNCTIONS:
        for body in _created_bodies(name):
            assert "IF session_user <> 'everydayai' THEN" in body
    assert "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'everydayai')" in SQL
    legacy_grant = SQL[SQL.index("DO $legacy_compatibility$"):SQL.index(
        "$legacy_compatibility$;", SQL.index("DO $legacy_compatibility$")
    )]
    for name in PUBLIC_FUNCTIONS:
        assert f"{name}(" in legacy_grant
    assert "TO everydayai;" in legacy_grant


def test_only_wecom_role_receives_message_facades() -> None:
    grant = SQL[SQL.index(
        "GRANT EXECUTE ON FUNCTION\n    resolve_wecom_conversation"
    ):SQL.index("TO everydayai_wecom_runtime;", SQL.index(
        "GRANT EXECUTE ON FUNCTION\n    resolve_wecom_conversation"
    ))]
    assert "everydayai_runtime" not in grant
    assert "everydayai_worker" not in grant
    assert "TO PUBLIC" not in SQL


def test_activity_wrapper_validates_role_identity_and_enums() -> None:
    body = _created_bodies("record_user_activity")[0]
    assert "tenant_database_role_matches_scope()" in body
    assert "tenant_actor_user_id() IS DISTINCT FROM p_user_id" in body
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in body
    assert "session_user = 'everydayai_wecom_runtime'" in body
    for value in (
        "login_success", "conversation_created", "message_sent",
        "task_created", "wecom_message_received", "file_uploaded",
    ):
        assert f"'{value}'" in body


def test_activity_table_is_not_directly_available_after_wrapper() -> None:
    assert (
        "REVOKE ALL ON TABLE user_activity_events\n"
        "FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;"
        in SQL
    )
    assert "TO everydayai_runtime, everydayai_worker;" in SQL


def test_rollback_drops_wrappers_and_restores_all_core_names() -> None:
    for name in PUBLIC_FUNCTIONS:
        assert f"DROP FUNCTION {name}(" in ROLLBACK
    for name in CORE_FUNCTIONS:
        assert f"ALTER FUNCTION {name}(" in ROLLBACK
    assert "RESET search_path;" in ROLLBACK
    assert "GRANT INSERT ON TABLE user_activity_events TO everydayai_runtime;" in ROLLBACK
    assert ROLLBACK.rstrip().endswith("RESET ROLE;")

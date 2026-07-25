"""Runtime/Message 第二批所有权脚本静态与执行合同。"""

from pathlib import Path
import os
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
TRANSFER = ROOT / "deploy/transfer-runtime-message-ownership.sh"
ROLLBACK = ROOT / "deploy/rollback-runtime-message-ownership.sh"
TABLES = {
    "users", "organizations", "org_members", "org_configs",
    "org_invitations",
    "wecom_user_mappings", "wecom_chat_targets", "conversations",
    "messages", "tasks", "credits_history", "credit_transactions",
    "image_generations", "detail_projects", "detail_project_images",
    "refresh_tokens", "user_subscriptions", "user_memory_settings",
}
FUNCTIONS = {
    "_prepare_generation_messages", "_prepare_generation_tasks",
    "claim_message_generation_request", "prepare_generation",
    "attach_generation_external_task", "fail_prepared_generation_task",
    "enqueue_generation_turn", "bind_generation_turn",
    "close_generation_turn", "cancel_generation_turn",
    "deduct_credits_atomic", "atomic_refund_credits",
    "partial_refund_credits", "increment_message_count",
    "record_user_activity", "resolve_wecom_conversation",
    "stage_wecom_attachment_v2", "enqueue_wecom_generation_turn_v2",
    "update_wecom_conversation_setting", "wecom_get_or_create_user",
    "claim_legacy_wecom_conversation", "current_attachment_parts",
    "bind_task_attachments", "enqueue_wecom_task_record",
    "renew_generation_lease", "update_generation_progress",
    "fail_generation_turn", "commit_generation_turn_with_context_v2",
    "commit_generation_turn",
}


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sql_path = tmp_path / "captured.sql"
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        f"#!/bin/sh\nprintf '%s' \"$*\" > '{tmp_path / 'psql-args'}'\n"
        f"cat > '{sql_path}'\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TENANT_DB_ADMIN_URL": "postgresql://admin-secret@localhost/everydayai",
        "LEGACY_DATABASE_OWNER": "everydayai",
    }, sql_path


def _run(
    script: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_transfer_requires_admin_url(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    del env["TENANT_DB_ADMIN_URL"]

    result = _run(TRANSFER, env)

    assert result.returncode == 1
    assert "TENANT_DB_ADMIN_URL" in result.stderr


def test_transfer_rejects_unsafe_legacy_owner(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["LEGACY_DATABASE_OWNER"] = "everydayai; DROP ROLE x"

    result = _run(TRANSFER, env)

    assert result.returncode == 1
    assert "不是合法 PostgreSQL 角色名" in result.stderr


def test_transfer_preflights_exact_tables_and_functions(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    table_block = sql.split(
        "target_tables CONSTANT TEXT[] := ARRAY[", 1,
    )[1].split("];", 1)[0]
    function_block = sql.split(
        "target_functions CONSTANT TEXT[] := ARRAY[", 1,
    )[1].split("];", 1)[0]
    assert set(re.findall(r"'([a-z_]+)'", table_block)) == TABLES
    assert set(re.findall(r"'([a-z0-9_]+)\(", function_block)) == FUNCTIONS
    assert (
        "'enqueue_wecom_generation_turn_v2"
        "(jsonb,uuid,uuid,uuid,jsonb,jsonb)'"
    ) in function_block
    assert (
        "'enqueue_wecom_generation_turn_v2"
        "(jsonb,uuid,uuid,uuid,jsonb,jsonb,uuid)'"
    ) in function_block
    assert "RUNTIME_MESSAGE_TABLE_MISSING" in sql
    assert "RUNTIME_MESSAGE_FUNCTION_MISSING" in sql
    assert "RUNTIME_MESSAGE_SEQUENCE_OWNER_UNEXPECTED" in sql
    for signature in (
        "renew_generation_lease(uuid,uuid,integer)",
        "update_generation_progress(uuid,uuid,text,jsonb)",
        "fail_generation_turn(uuid,uuid,text,text)",
        "commit_generation_turn_with_context_v2(",
        "close_generation_turn(uuid,uuid,uuid)",
    ):
        assert signature in function_block


def test_transfer_handles_owned_column_sequences_without_guessing(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert "dependency.deptype IN ('a', 'i')" in sql
    assert "ALTER SEQUENCE public.%I OWNER TO everydayai_owner" in sql
    assert "ALTER TABLE public.%I OWNER TO everydayai_owner" in sql
    assert "ALTER FUNCTION public.%s OWNER TO everydayai_owner" in sql
    assert "ON ALL SEQUENCES IN SCHEMA public" not in sql
    assert (
        "REVOKE ALL ON SEQUENCE public.%I FROM everydayai_runtime, "
        "everydayai_wecom_runtime, everydayai_worker"
    ) in sql
    assert "GRANT USAGE, SELECT, UPDATE ON SEQUENCE public.%I TO %I" in sql


def test_transfer_revokes_new_roles_and_preserves_legacy_service(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert (
        "FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;"
        in sql
    )
    assert "TO everydayai;" in sql
    assert (
        "REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, "
        "everydayai_runtime, everydayai_wecom_runtime, everydayai_worker"
    ) in sql
    assert "GRANT EXECUTE ON FUNCTION public.%s TO %I" in sql
    assert "ENABLE ROW LEVEL SECURITY" not in sql
    assert "FORCE ROW LEVEL SECURITY" not in sql


def test_admin_url_is_not_exposed_in_psql_arguments(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    arguments = (tmp_path / "psql-args").read_text(encoding="utf-8")
    assert env["TENANT_DB_ADMIN_URL"] not in arguments
    assert sql_path.read_text(encoding="utf-8").startswith(
        "\\set ON_ERROR_STOP on\n"
    )


def test_transfer_rejects_whitespace_in_admin_url(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["TENANT_DB_ADMIN_URL"] += "\tunsafe"

    result = _run(TRANSFER, env)

    assert result.returncode == 1
    assert "不能包含空白字符" in result.stderr


def test_rollback_requires_destructive_and_service_guards(
    tmp_path: Path,
) -> None:
    env, _ = _environment(tmp_path)

    result = _run(ROLLBACK, env)
    assert result.returncode == 1
    assert "ALLOW_RUNTIME_MESSAGE_OWNERSHIP_ROLLBACK=true" in result.stderr

    env["ALLOW_RUNTIME_MESSAGE_OWNERSHIP_ROLLBACK"] = "true"
    result = _run(ROLLBACK, env)
    assert result.returncode == 1
    assert "RUNTIME_MESSAGE_SERVICES_RESTORED=true" in result.stderr


def test_rollback_restores_objects_and_rejects_force_rls(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)
    env["ALLOW_RUNTIME_MESSAGE_OWNERSHIP_ROLLBACK"] = "true"
    env["RUNTIME_MESSAGE_SERVICES_RESTORED"] = "true"

    result = _run(ROLLBACK, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert "DISABLE_FORCE_RLS_BEFORE_RUNTIME_MESSAGE_ROLLBACK" in sql
    assert "'ALTER TABLE public.%I OWNER TO %I'" in sql
    assert "'ALTER SEQUENCE public.%I OWNER TO %I'" in sql
    assert "'ALTER FUNCTION public.%s OWNER TO %I'" in sql
    assert sql.index(
        "'ALTER TABLE public.%I OWNER TO %I'"
    ) < sql.index(
        "'ALTER SEQUENCE public.%I OWNER TO %I'"
    )
    assert "REVOKE USAGE ON SCHEMA public FROM everydayai_wecom_runtime;" in sql
    assert sql.startswith("\\set ON_ERROR_STOP on\n")

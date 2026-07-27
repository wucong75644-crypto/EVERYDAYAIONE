"""Real PostgreSQL migration ordering, permissions, RLS, and rollback contract."""

from __future__ import annotations

import pytest

from tests.test_agent_runtime_model_attempt_postgres_external import (
    MIGRATIONS,
    ROLLBACKS,
    create_running_step,
    decoded,
    ensure_database,
    execute,
    execute_file,
    prepare_attempt,
    runtime,
)

pytestmark = pytest.mark.external


def test_permissions_force_rls_and_no_table_grants() -> None:
    ensure_database()
    rows = execute(
        """
        SELECT relname,relforcerowsecurity
          FROM pg_class
         WHERE relname IN ('agent_model_attempts','agent_model_credit_settlements')
         ORDER BY relname
        """
    )
    grants = execute(
        """
        SELECT count(*) FROM information_schema.role_table_grants
         WHERE table_name IN ('agent_model_attempts','agent_model_credit_settlements')
           AND grantee IN (
             'everydayai_runtime','everydayai_wecom_runtime','everydayai_worker',
             'everydayai_sync','everydayai'
           )
        """
    )[0][0]
    assert rows == [
        ("agent_model_attempts", True),
        ("agent_model_credit_settlements", True),
    ]
    assert grants == 0


def test_cancel_grants_and_personal_scope_contract_are_preserved() -> None:
    ensure_database()
    grants = execute(
        """
        SELECT role_name,has_function_privilege(
            role_name,'cancel_agent_run(uuid,bigint,text)','EXECUTE'
        )
          FROM unnest(ARRAY[
            'everydayai_runtime','everydayai_wecom_runtime','everydayai_worker',
            'everydayai_sync','everydayai'
          ]) role_name
        """
    )
    assert grants == [
        ("everydayai_runtime", True),
        ("everydayai_wecom_runtime", True),
        ("everydayai_worker", True),
        ("everydayai_sync", False),
        ("everydayai", False),
    ]
    facts = create_running_step()
    cancelled = decoded(
        runtime(
            "SELECT cancel_agent_run(%s,%s,'runtime_cancel');",
            facts["user_id"],
            (facts["run_id"], facts["run_version"]),
        )[-1][0]
    )
    assert cancelled["outcome"] == "cancelled"


def test_real_rollback_reverse_order_and_reapply() -> None:
    ensure_database()
    facts = create_running_step()
    prepare_attempt(facts)
    with pytest.raises(Exception, match="ROLLBACK_FACTS_PRESENT"):
        execute_file(ROLLBACKS[2])
    execute(
        """
        SET ROLE everydayai_owner;
        TRUNCATE agent_model_credit_settlements,agent_model_attempts CASCADE;
        RESET ROLE;
        """
    )
    for rollback in ROLLBACKS:
        execute_file(rollback)
    assert execute(
        "SELECT to_regclass('public.agent_model_attempts'),"
        "to_regclass('public.agent_model_credit_settlements')"
    )[0] == (None, None)
    for migration in MIGRATIONS:
        execute_file(migration)
    assert execute(
        "SELECT to_regclass('public.agent_model_attempts'),"
        "to_regclass('public.agent_model_credit_settlements')"
    )[0] == ("agent_model_attempts", "agent_model_credit_settlements")

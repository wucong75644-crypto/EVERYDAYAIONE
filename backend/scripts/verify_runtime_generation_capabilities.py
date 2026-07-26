"""Fail deployment when the Web runtime generation ACL chain is incomplete."""

from __future__ import annotations

import os
from typing import Any

import psycopg


EXPECTED_RUNTIME_ROLE = "everydayai_runtime"
EXPECTED_WORKER_ROLE = "everydayai_worker"
EXPECTED_OWNER = "everydayai_owner"
PUBLIC_FUNCTIONS = (
    "public.prepare_generation(uuid,text,uuid,uuid,uuid,uuid,jsonb,jsonb,jsonb)",
    "public.attach_generation_external_task(uuid,text,uuid,uuid,text,jsonb)",
    "public.fail_prepared_generation_task(uuid,text,text,uuid)",
)
PRIVATE_FUNCTIONS = (
    "public._prepare_generation_owner("
    "uuid,text,uuid,uuid,uuid,uuid,jsonb,jsonb,jsonb)",
    "public._prepare_generation_messages(text,uuid,uuid,uuid,jsonb,jsonb)",
    "public._prepare_generation_tasks("
    "jsonb,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid)",
    "public._attach_generation_external_task_owner("
    "uuid,text,uuid,uuid,text,jsonb)",
    "public._fail_prepared_generation_task_owner(uuid,text,text,uuid)",
)
PRIVATE_SEQUENCE = "public.task_queue_sequence_seq"
WORKER_FUNCTIONS = (
    "public.worker_discover_media_tasks(integer)",
    "public.worker_discover_legacy_active_tasks()",
    "public.worker_get_media_task(text)",
    "public.worker_touch_media_task(text)",
    "public.worker_claim_media_task_completion(text,integer)",
    "public.worker_settle_media_batch_item(text,integer,text,jsonb,text)",
    "public.worker_fail_legacy_stale_task(uuid,text,jsonb)",
    "public.worker_get_media_batch_message(text)",
    "public.worker_commit_media_batch_message(text,jsonb,text)",
    "public.worker_commit_video_terminal(text,integer,text,jsonb,text,text)",
    "public.worker_prepare_media_retry(text,integer,text)",
    "public.worker_abort_media_retry(text,integer,uuid)",
    "public.worker_commit_media_retry(text,integer,text,text,jsonb,uuid)",
    "public.worker_record_media_metric("
    "uuid,text,text,integer,integer,integer,text,jsonb,boolean,text)",
)


class RuntimeGenerationCapabilityError(RuntimeError):
    """The runtime database role cannot complete generation preparation."""


def verify_capabilities(connection: psycopg.Connection[Any]) -> None:
    """Verify that generation preparation is exposed only by its safe facade."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        session_user = cursor.fetchone()[0]
        if session_user != EXPECTED_RUNTIME_ROLE:
            raise RuntimeGenerationCapabilityError(
                f"RUNTIME_GENERATION_ROLE_INVALID:{session_user}"
            )

        cursor.execute(
            """
            SELECT procedure.oid::REGPROCEDURE::TEXT,
                   owner_role.rolname, procedure.prosecdef,
                   has_function_privilege(
                       session_user, procedure.oid, 'EXECUTE'
                   )
              FROM pg_proc procedure
              JOIN pg_roles owner_role ON owner_role.oid = procedure.proowner
             WHERE procedure.oid = ANY (
                       SELECT to_regprocedure(signature)
                         FROM unnest(%s::TEXT[]) AS signature
                   )
            """,
            (list(PUBLIC_FUNCTIONS),),
        )
        public_capabilities = cursor.fetchall()
        if len(public_capabilities) != len(PUBLIC_FUNCTIONS) or any(
            owner != EXPECTED_OWNER or not security_definer or not executable
            for _, owner, security_definer, executable in public_capabilities
        ):
            raise RuntimeGenerationCapabilityError(
                "RUNTIME_GENERATION_FACADE_INVALID"
            )

        cursor.execute(
            """
            SELECT signature, to_regprocedure(signature) IS NOT NULL,
                   CASE WHEN to_regprocedure(signature) IS NULL THEN FALSE
                        ELSE has_function_privilege(
                            session_user, to_regprocedure(signature), 'EXECUTE'
                        )
                   END
              FROM unnest(%s::TEXT[]) AS signature
            """,
            (list(PRIVATE_FUNCTIONS),),
        )
        invalid_private = [
            signature
            for signature, exists, executable in cursor.fetchall()
            if not exists or executable
        ]
        if invalid_private:
            raise RuntimeGenerationCapabilityError(
                "RUNTIME_GENERATION_PRIVATE_FUNCTION_EXPOSED:"
                + ",".join(invalid_private)
            )

        cursor.execute(
            """
            SELECT to_regclass(%s) IS NOT NULL,
                   has_sequence_privilege(session_user, %s, 'USAGE')
            """,
            (PRIVATE_SEQUENCE, PRIVATE_SEQUENCE),
        )
        sequence_exists, sequence_executable = cursor.fetchone()
        if not sequence_exists or sequence_executable:
            raise RuntimeGenerationCapabilityError(
                "RUNTIME_GENERATION_PRIVATE_SEQUENCE_EXPOSED:"
                + PRIVATE_SEQUENCE
            )


def verify_worker_capabilities(connection: psycopg.Connection[Any]) -> None:
    """Verify the complete Worker media polling and settlement chain."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        session_user = cursor.fetchone()[0]
        if session_user != EXPECTED_WORKER_ROLE:
            raise RuntimeGenerationCapabilityError(
                f"WORKER_MEDIA_ROLE_INVALID:{session_user}"
            )
        cursor.execute(
            """
            SELECT signature, to_regprocedure(signature) IS NOT NULL,
                   CASE WHEN to_regprocedure(signature) IS NULL THEN FALSE
                        ELSE (
                            SELECT owner_role.rolname = %s
                                   AND procedure.prosecdef
                                   AND has_function_privilege(
                                       session_user, procedure.oid, 'EXECUTE'
                                   )
                              FROM pg_proc procedure
                              JOIN pg_roles owner_role
                                ON owner_role.oid = procedure.proowner
                             WHERE procedure.oid = to_regprocedure(signature)
                        )
                   END
              FROM unnest(%s::TEXT[]) AS signature
            """,
            (EXPECTED_OWNER, list(WORKER_FUNCTIONS)),
        )
        invalid = [
            signature
            for signature, exists, valid in cursor.fetchall()
            if not exists or not valid
        ]
        if invalid:
            raise RuntimeGenerationCapabilityError(
                "WORKER_MEDIA_CAPABILITY_INVALID:" + ",".join(invalid)
            )


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    worker_database_url = os.environ.get("WORKER_DATABASE_URL", "")
    if not database_url:
        print("❌ 缺少 Runtime DATABASE_URL")
        return 1
    if not worker_database_url:
        print("❌ 缺少 WORKER_DATABASE_URL")
        return 1
    try:
        with psycopg.connect(database_url) as connection:
            verify_capabilities(connection)
        with psycopg.connect(worker_database_url) as connection:
            verify_worker_capabilities(connection)
    except (psycopg.Error, RuntimeGenerationCapabilityError) as error:
        print(f"❌ Runtime 生成能力检查失败: {error}")
        return 1
    print("✅ Runtime 提交与 Worker 媒体结算能力链检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

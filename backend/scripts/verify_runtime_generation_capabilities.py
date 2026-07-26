"""Fail deployment when the Web runtime generation ACL chain is incomplete."""

from __future__ import annotations

import os
from typing import Any

import psycopg


EXPECTED_ROLE = "everydayai_runtime"
EXPECTED_OWNER = "everydayai_owner"
PUBLIC_FUNCTION = (
    "public.prepare_generation(uuid,text,uuid,uuid,uuid,uuid,jsonb,jsonb,jsonb)"
)
PRIVATE_FUNCTIONS = (
    "public._prepare_generation_owner("
    "uuid,text,uuid,uuid,uuid,uuid,jsonb,jsonb,jsonb)",
    "public._prepare_generation_messages(text,uuid,uuid,uuid,jsonb,jsonb)",
    "public._prepare_generation_tasks("
    "jsonb,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid)",
)
PRIVATE_SEQUENCE = "public.task_queue_sequence_seq"


class RuntimeGenerationCapabilityError(RuntimeError):
    """The runtime database role cannot complete generation preparation."""


def verify_capabilities(connection: psycopg.Connection[Any]) -> None:
    """Verify that generation preparation is exposed only by its safe facade."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        session_user = cursor.fetchone()[0]
        if session_user != EXPECTED_ROLE:
            raise RuntimeGenerationCapabilityError(
                f"RUNTIME_GENERATION_ROLE_INVALID:{session_user}"
            )

        cursor.execute(
            """
            SELECT owner_role.rolname, procedure.prosecdef,
                   has_function_privilege(
                       session_user, procedure.oid, 'EXECUTE'
                   )
              FROM pg_proc procedure
              JOIN pg_roles owner_role ON owner_role.oid = procedure.proowner
             WHERE procedure.oid = to_regprocedure(%s)
            """,
            (PUBLIC_FUNCTION,),
        )
        public_capability = cursor.fetchone()
        if public_capability != (EXPECTED_OWNER, True, True):
            raise RuntimeGenerationCapabilityError(
                "RUNTIME_GENERATION_FACADE_INVALID:" + PUBLIC_FUNCTION
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


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("❌ 缺少 Runtime DATABASE_URL")
        return 1
    try:
        with psycopg.connect(database_url) as connection:
            verify_capabilities(connection)
    except (psycopg.Error, RuntimeGenerationCapabilityError) as error:
        print(f"❌ Runtime 生成能力检查失败: {error}")
        return 1
    print("✅ Runtime 生成安全能力门面检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

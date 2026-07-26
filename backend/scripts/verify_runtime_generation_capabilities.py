"""Fail deployment when the Web runtime generation ACL chain is incomplete."""

from __future__ import annotations

import os
from typing import Any

import psycopg


EXPECTED_ROLE = "everydayai_runtime"
REQUIRED_FUNCTIONS = (
    "public.prepare_generation(uuid,text,uuid,uuid,uuid,uuid,jsonb,jsonb,jsonb)",
    "public._prepare_generation_messages(text,uuid,uuid,uuid,jsonb,jsonb)",
    "public._prepare_generation_tasks("
    "jsonb,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid)",
)
REQUIRED_SEQUENCE = "public.task_queue_sequence_seq"


class RuntimeGenerationCapabilityError(RuntimeError):
    """The runtime database role cannot complete generation preparation."""


def verify_capabilities(connection: psycopg.Connection[Any]) -> None:
    """Verify the complete function and sequence capability chain."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        session_user = cursor.fetchone()[0]
        if session_user != EXPECTED_ROLE:
            raise RuntimeGenerationCapabilityError(
                f"RUNTIME_GENERATION_ROLE_INVALID:{session_user}"
            )

        cursor.execute(
            """
            SELECT signature
              FROM unnest(%s::TEXT[]) AS signature
             WHERE to_regprocedure(signature) IS NULL
                OR NOT has_function_privilege(
                    session_user, to_regprocedure(signature), 'EXECUTE'
                )
            """,
            (list(REQUIRED_FUNCTIONS),),
        )
        missing = [row[0] for row in cursor.fetchall()]
        if missing:
            raise RuntimeGenerationCapabilityError(
                "RUNTIME_GENERATION_FUNCTION_CAPABILITY_MISSING:"
                + ",".join(missing)
            )

        cursor.execute(
            """
            SELECT to_regclass(%s) IS NOT NULL
               AND has_sequence_privilege(session_user, %s, 'USAGE')
            """,
            (REQUIRED_SEQUENCE, REQUIRED_SEQUENCE),
        )
        if not cursor.fetchone()[0]:
            raise RuntimeGenerationCapabilityError(
                "RUNTIME_GENERATION_SEQUENCE_CAPABILITY_MISSING:"
                + REQUIRED_SEQUENCE
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
    print("✅ Runtime 生成函数与队列序列能力检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

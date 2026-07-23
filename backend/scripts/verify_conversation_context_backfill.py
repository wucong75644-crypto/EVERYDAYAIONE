"""只读验证统一 ConversationItem 历史回填是否满足硬切换条件。"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from psycopg import sql

if __package__:
    from .backfill_conversation_context_items import load_env
else:
    from backfill_conversation_context_items import load_env


@dataclass(frozen=True)
class VerificationResult:
    """历史回填覆盖率与结构不变量统计。"""

    eligible_messages: int
    projected_messages: int
    missing_messages: int
    orphan_tool_results: int
    duplicate_sequences: int
    invalid_item_revisions: int
    missing_artifacts: int

    @property
    def passed(self) -> bool:
        return all(
            value == 0
            for value in (
                self.missing_messages,
                self.orphan_tool_results,
                self.duplicate_sequences,
                self.invalid_item_revisions,
                self.missing_artifacts,
            )
        )

    def to_dict(self) -> dict[str, int | bool]:
        return {**asdict(self), "passed": self.passed}


def verify_backfill(
    conn: psycopg.Connection[Any],
    conversation_id: uuid.UUID | None = None,
) -> VerificationResult:
    """在只读事务内核对消息覆盖和 ContextItem/Artifact 不变量。"""
    scope = sql.SQL("")
    params: dict[str, Any] = {}
    if conversation_id is not None:
        scope = sql.SQL(" AND {alias}.conversation_id = %(conversation_id)s")
        params["conversation_id"] = conversation_id

    query = sql.SQL(
        """
        WITH eligible AS (
            SELECT m.id, m.conversation_id
              FROM messages m
             WHERE m.message_kind = 'conversation'
               AND m.context_revision > 0
               AND m.status::text IN ('completed', 'interrupted')
               {message_scope}
        ), projected AS (
            SELECT DISTINCT i.source_message_id
              FROM conversation_context_items i
              JOIN eligible e ON e.id = i.source_message_id
        ), orphan_results AS (
            SELECT i.id
              FROM conversation_context_items i
             WHERE i.item_type = 'tool_result'
               {item_scope}
               AND (
                    i.group_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1
                          FROM conversation_context_items call
                         WHERE call.conversation_id = i.conversation_id
                           AND call.group_id = i.group_id
                           AND call.item_type = 'tool_call'
                    )
               )
        ), duplicate_sequences AS (
            SELECT i.conversation_id, i.sequence
              FROM conversation_context_items i
             WHERE TRUE {item_scope}
             GROUP BY i.conversation_id, i.sequence
            HAVING COUNT(*) > 1
        ), invalid_revisions AS (
            SELECT i.id
              FROM conversation_context_items i
             WHERE TRUE {item_scope}
               AND (
                    i.context_revision <= 0
                    OR i.local_sequence NOT BETWEEN 0 AND 999
                    OR i.sequence <> i.context_revision * 1000 + i.local_sequence
               )
        ), artifact_references AS (
            SELECT i.conversation_id,
                   COALESCE(
                       i.payload->>'artifact_id',
                       i.payload#>>'{{arguments,artifact_id}}'
                   )::uuid AS artifact_id
              FROM conversation_context_items i
             WHERE TRUE {item_scope}
               AND COALESCE(
                       i.payload->>'artifact_id',
                       i.payload#>>'{{arguments,artifact_id}}'
                   ) IS NOT NULL
        ), missing_artifacts AS (
            SELECT r.conversation_id, r.artifact_id
              FROM artifact_references r
              LEFT JOIN conversation_artifacts a
                ON a.conversation_id = r.conversation_id
               AND a.id = r.artifact_id
             WHERE a.id IS NULL
        )
        SELECT
            (SELECT COUNT(*) FROM eligible) AS eligible_messages,
            (SELECT COUNT(*) FROM projected) AS projected_messages,
            (SELECT COUNT(*) FROM eligible e
              WHERE NOT EXISTS (
                  SELECT 1 FROM projected p WHERE p.source_message_id = e.id
              )) AS missing_messages,
            (SELECT COUNT(*) FROM orphan_results) AS orphan_tool_results,
            (SELECT COUNT(*) FROM duplicate_sequences) AS duplicate_sequences,
            (SELECT COUNT(*) FROM invalid_revisions) AS invalid_item_revisions,
            (SELECT COUNT(*) FROM missing_artifacts) AS missing_artifacts
        """
    ).format(
        message_scope=scope.format(alias=sql.Identifier("m")),
        item_scope=scope.format(alias=sql.Identifier("i")),
    )
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("verification query returned no result")
    return VerificationResult(**{key: int(value) for key, value in row.items()})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversation-id", type=uuid.UUID)
    args = parser.parse_args()
    load_env()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    with psycopg.connect(database_url) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        result = verify_backfill(conn, args.conversation_id)
        conn.rollback()
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

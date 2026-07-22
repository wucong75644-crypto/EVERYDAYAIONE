"""历史生成消息 Turn 关系回填。

默认 dry-run；只有显式 --apply 才分批写入。审计文件不包含消息正文。
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKPOINT = BACKEND_ROOT / "tmp" / "generation_turn_backfill.json"
DEFAULT_AUDIT = BACKEND_ROOT / "tmp" / "generation_turn_backfill_audit.jsonl"


@dataclass(frozen=True)
class RepairPlan:
    output_message_id: str
    input_message_id: str | None
    turn_id: str | None
    status: str
    reason: str
    old_turn_id: str | None = None
    old_reply_to_message_id: str | None = None


@dataclass
class BackfillStats:
    scanned: int = 0
    repaired: int = 0
    already_valid: int = 0
    conflict: int = 0
    ambiguous: int = 0
    failed: int = 0
    reasons: Counter[str] = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--org-id")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--audit-file", type=Path, default=DEFAULT_AUDIT)
    return parser.parse_args()


def classify_candidate(row: dict[str, Any]) -> RepairPlan:
    """按权威级别选择唯一关系；任何冲突都不生成修复计划。"""
    output_id = str(row["output_message_id"])
    old_turn = _optional_str(row.get("output_turn_id"))
    old_reply = _optional_str(row.get("output_reply_to_message_id"))
    sources = (
        ("task", _relations(row.get("task_relations"))),
        ("reply", _relations(row.get("reply_relations"))),
        ("same_turn", _relations(row.get("same_turn_relations"))),
        ("previous", _relations(row.get("previous_relations"))),
    )
    for source, relations in sources:
        if len(relations) > 1:
            return RepairPlan(
                output_id, None, None, "conflict", f"{source}_relations_conflict",
                old_turn, old_reply,
            )
        if not relations:
            continue
        input_id, turn_id = relations[0]
        if not input_id or not turn_id:
            continue
        if old_reply and old_reply != input_id:
            return RepairPlan(
                output_id, input_id, turn_id, "conflict", "existing_reply_conflict",
                old_turn, old_reply,
            )
        if old_turn and old_turn != turn_id:
            return RepairPlan(
                output_id, input_id, turn_id, "conflict", "existing_turn_conflict",
                old_turn, old_reply,
            )
        status = "already_valid" if old_reply == input_id and old_turn == turn_id else "repair"
        return RepairPlan(
            output_id, input_id, turn_id, status, source, old_turn, old_reply,
        )
    return RepairPlan(
        output_id, None, None, "ambiguous", "no_deterministic_relation",
        old_turn, old_reply,
    )


def fetch_batch(
    conn: psycopg.Connection[Any], *, cursor_value: dict[str, str] | None,
    batch_size: int, org_id: str | None, lock: bool,
) -> list[dict[str, Any]]:
    conditions = ["o.role = 'assistant'", "(o.turn_id IS NULL OR o.reply_to_message_id IS NULL)"]
    params: list[Any] = []
    if cursor_value:
        conditions.append("(o.created_at, o.id) > (%s::timestamptz, %s::uuid)")
        params.extend([cursor_value["created_at"], cursor_value["id"]])
    if org_id:
        conditions.append("o.org_id = %s::uuid")
        params.append(org_id)
    params.append(batch_size)
    # checkpoint 必须连续前进；SKIP LOCKED 会让被跳过行永久落在游标之前。
    lock_clause = " FOR UPDATE OF o" if lock else ""
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_candidate_sql(conditions, lock_clause), params)
        return list(cursor.fetchall())


def apply_plan(
    conn: psycopg.Connection[Any], plan: RepairPlan,
) -> bool:
    """只填空值或已等于计划值的字段，避免覆盖并发合法更新。"""
    if plan.status != "repair" or not plan.input_message_id or not plan.turn_id:
        return False
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE messages
               SET turn_id = %s::uuid, reply_to_message_id = %s::uuid,
                   updated_at = NOW()
             WHERE id = %s::uuid AND role = 'assistant'
               AND (turn_id IS NULL OR turn_id = %s::uuid)
               AND (reply_to_message_id IS NULL OR reply_to_message_id = %s::uuid)
            RETURNING id
            """,
            (
                plan.turn_id, plan.input_message_id, plan.output_message_id,
                plan.turn_id, plan.input_message_id,
            ),
        )
        return cursor.fetchone() is not None


def audit_invariants(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """统计非空关系中的跨会话、角色和 Turn 不一致。"""
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""
            SELECT
              COUNT(*) FILTER (WHERE i.id IS NULL)::bigint AS missing_input,
              COUNT(*) FILTER (WHERE i.id IS NOT NULL AND i.role <> 'user')::bigint AS invalid_role,
              COUNT(*) FILTER (WHERE i.id IS NOT NULL AND (
                i.conversation_id IS DISTINCT FROM o.conversation_id OR
                i.user_id IS DISTINCT FROM o.user_id OR
                i.org_id IS DISTINCT FROM o.org_id))::bigint AS scope_mismatch,
              COUNT(*) FILTER (WHERE i.id IS NOT NULL AND
                i.turn_id IS DISTINCT FROM o.turn_id)::bigint AS turn_mismatch
            FROM messages o
            LEFT JOIN messages i ON i.id = o.reply_to_message_id
            WHERE o.role = 'assistant' AND o.reply_to_message_id IS NOT NULL
        """)
        row = cursor.fetchone()
    return {key: int(value) for key, value in row.items()}


def run(
    conn: psycopg.Connection[Any], *, apply: bool, batch_size: int,
    limit: int | None, org_id: str | None, checkpoint_path: Path,
    audit_path: Path,
) -> tuple[BackfillStats, dict[str, int], dict[str, int]]:
    stats = BackfillStats()
    checkpoint = load_checkpoint(checkpoint_path)
    before = audit_invariants(conn)
    while limit is None or stats.scanned < limit:
        size = min(batch_size, limit - stats.scanned) if limit else batch_size
        rows = fetch_batch(
            conn, cursor_value=checkpoint, batch_size=size,
            org_id=org_id, lock=apply,
        )
        if not rows:
            break
        batch_audit = []
        try:
            plans = []
            for row in rows:
                plan = classify_candidate(row)
                plans.append(plan)
                stats.scanned += 1
                stats.reasons[plan.reason] += 1
                _count_plan(stats, plan)
                if apply and plan.status == "repair":
                    batch_audit.append(_audit_record(plan))
            if apply:
                append_audit(audit_path, batch_audit)
                for plan in plans:
                    if plan.status != "repair":
                        continue
                    if not apply_plan(conn, plan):
                        raise RuntimeError("GENERATION_TURN_BACKFILL_CONCURRENT_CONFLICT")
                conn.commit()
            else:
                conn.rollback()
        except Exception:
            conn.rollback()
            stats.failed += len(rows)
            raise
        last = rows[-1]
        checkpoint = {
            "created_at": _iso_value(last["output_created_at"]),
            "id": str(last["output_message_id"]),
        }
        if apply:
            save_checkpoint(checkpoint_path, checkpoint)
    after = audit_invariants(conn)
    return stats, before, after


def load_checkpoint(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"created_at", "id"}:
        raise ValueError("checkpoint is invalid")
    return {key: str(item) for key, item in value.items()}


def save_checkpoint(path: Path, value: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def append_audit(path: Path, records: Iterable[dict[str, Any]]) -> None:
    values = list(records)
    if not values:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for record in values:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


def load_env() -> None:
    for path in (BACKEND_ROOT / ".env", BACKEND_ROOT.parent / ".env"):
        if path.exists():
            load_dotenv(path, override=False)


def main() -> int:
    args = parse_args()
    if args.batch_size < 1 or args.batch_size > 1000:
        raise ValueError("batch-size must be between 1 and 1000")
    if args.limit is not None and args.limit < 1:
        raise ValueError("limit must be positive")
    load_env()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    with psycopg.connect(database_url) as conn:
        stats, before, after = run(
            conn, apply=args.apply, batch_size=args.batch_size, limit=args.limit,
            org_id=args.org_id, checkpoint_path=args.checkpoint,
            audit_path=args.audit_file,
        )
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "stats": {**asdict(stats), "reasons": dict(stats.reasons)},
        "invariants_before": before, "invariants_after": after,
    }, sort_keys=True))
    return 0


def _relations(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict) and item.get("input_id") and item.get("turn_id"):
            pair = (str(item["input_id"]), str(item["turn_id"]))
            if pair not in result:
                result.append(pair)
    return result


def _count_plan(stats: BackfillStats, plan: RepairPlan) -> None:
    name = "repaired" if plan.status == "repair" else plan.status
    setattr(stats, name, getattr(stats, name) + 1)


def _audit_record(plan: RepairPlan) -> dict[str, Any]:
    return {
        "output_message_id": plan.output_message_id,
        "old_turn_id": plan.old_turn_id,
        "old_reply_to_message_id": plan.old_reply_to_message_id,
        "new_turn_id": plan.turn_id,
        "new_reply_to_message_id": plan.input_message_id,
        "reason": plan.reason,
    }


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _iso_value(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _candidate_sql(conditions: list[str], lock_clause: str) -> str:
    return """
        SELECT o.id AS output_message_id, o.created_at AS output_created_at,
               o.turn_id AS output_turn_id,
               o.reply_to_message_id AS output_reply_to_message_id,
               COALESCE(task_rel.items, '[]'::jsonb) AS task_relations,
               COALESCE(reply_rel.items, '[]'::jsonb) AS reply_relations,
               COALESCE(turn_rel.items, '[]'::jsonb) AS same_turn_relations,
               COALESCE(previous_rel.items, '[]'::jsonb) AS previous_relations
          FROM messages o
          LEFT JOIN LATERAL (
            SELECT jsonb_agg(DISTINCT jsonb_build_object(
              'input_id', t.input_message_id, 'turn_id', t.turn_id)) AS items
              FROM tasks t JOIN messages i ON i.id = t.input_message_id
             WHERE t.assistant_message_id = o.id AND i.role = 'user'
               AND i.conversation_id = o.conversation_id
               AND i.user_id = o.user_id AND i.org_id IS NOT DISTINCT FROM o.org_id
               AND t.turn_id IS NOT NULL AND i.turn_id = t.turn_id
          ) task_rel ON TRUE
          LEFT JOIN LATERAL (
            SELECT jsonb_agg(jsonb_build_object(
              'input_id', i.id, 'turn_id', i.turn_id)) AS items
              FROM messages i WHERE i.id = o.reply_to_message_id AND i.role = 'user'
               AND i.conversation_id = o.conversation_id
               AND i.user_id = o.user_id AND i.org_id IS NOT DISTINCT FROM o.org_id
               AND i.turn_id IS NOT NULL
          ) reply_rel ON TRUE
          LEFT JOIN LATERAL (
            SELECT jsonb_agg(jsonb_build_object(
              'input_id', i.id, 'turn_id', i.turn_id)) AS items
              FROM messages i WHERE o.turn_id IS NOT NULL AND i.turn_id = o.turn_id
               AND i.role = 'user' AND i.conversation_id = o.conversation_id
               AND i.user_id = o.user_id AND i.org_id IS NOT DISTINCT FROM o.org_id
          ) turn_rel ON TRUE
          LEFT JOIN LATERAL (
            SELECT jsonb_agg(jsonb_build_object(
              'input_id', candidate.id, 'turn_id', candidate.turn_id)) AS items
              FROM LATERAL (
                SELECT i.id, i.turn_id FROM messages i
                 WHERE i.conversation_id = o.conversation_id AND i.role = 'user'
                   AND i.user_id = o.user_id AND i.org_id IS NOT DISTINCT FROM o.org_id
                   AND i.turn_id IS NOT NULL
                   AND (i.created_at, i.id) < (o.created_at, o.id)
                   AND NOT EXISTS (
                     SELECT 1 FROM messages between_message
                      WHERE between_message.conversation_id = o.conversation_id
                        AND between_message.role IN ('user', 'assistant')
                        AND (between_message.created_at, between_message.id) >
                            (i.created_at, i.id)
                        AND (between_message.created_at, between_message.id) <
                            (o.created_at, o.id))
                 ORDER BY i.created_at DESC, i.id DESC LIMIT 2
              ) candidate
          ) previous_rel ON TRUE
         WHERE """ + " AND ".join(conditions) + """
         ORDER BY o.created_at, o.id LIMIT %s
    """ + lock_clause


if __name__ == "__main__":
    raise SystemExit(main())

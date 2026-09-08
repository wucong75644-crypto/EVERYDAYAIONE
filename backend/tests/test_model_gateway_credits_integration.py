"""T5 最终结果复用 Actor 原子积分协议（必须显式启用隔离 PostgreSQL）。"""
import os

import pytest

from tests.test_conversation_actor_pause_snapshot_integration import _fixture

_DATABASE_URL = os.environ.get("CONVERSATION_ACTOR_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CONVERSATION_ACTOR_DB_TEST") != "1" or not _DATABASE_URL,
    reason="需要显式启用隔离 Actor PostgreSQL 测试库",
)


def test_retry_success_commit_is_idempotent_for_credits_and_usage():
    import psycopg
    from psycopg.types.json import Jsonb

    conn = psycopg.connect(_DATABASE_URL, autocommit=False)
    try:
        with conn.cursor() as cur:
            task = _fixture(cur)
            cur.execute("UPDATE users SET credits = 100 WHERE id = %s", (task["user_id"],))
            cur.execute("UPDATE tasks SET model_id = 'qwen3.5-plus' WHERE id = %s", (task["task_id"],))
            usage = {"prompt_tokens": 4, "completion_tokens": 2, "api_credits": 1.25}
            params = (
                task["task_id"], task["execution_token"], task["output_message_id"],
                Jsonb([{"type": "text", "text": "fallback success"}]), Jsonb(usage), 3,
            )
            for expected in ("committed", "already_committed"):
                cur.execute("SELECT commit_generation_turn(%s,%s,%s,%s,%s,%s)", params)
                assert cur.fetchone()[0]["outcome"] == expected
            cur.execute("SELECT credits FROM users WHERE id = %s", (task["user_id"],))
            assert cur.fetchone()[0] == 97
            cur.execute("SELECT change_amount, description FROM credits_history WHERE user_id = %s", (task["user_id"],))
            assert cur.fetchall() == [(-3, "Chat: qwen3.5-plus")]
            cur.execute("SELECT generation_params->'usage', generation_params->>'model', credits_cost FROM messages WHERE id = %s", (task["output_message_id"],))
            assert cur.fetchone() == (usage, "qwen3.5-plus", 3)
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("code", ["GENERATION_FAILED", "MODEL_PARTIAL_OUTPUT", "BUSINESS_ERROR", "MODEL_TIMEOUT"])
def test_final_model_failure_does_not_charge_or_refund(code):
    import psycopg

    conn = psycopg.connect(_DATABASE_URL, autocommit=False)
    try:
        with conn.cursor() as cur:
            task = _fixture(cur)
            cur.execute("UPDATE users SET credits = 100 WHERE id = %s", (task["user_id"],))
            cur.execute("SELECT fail_generation_turn(%s,%s,%s,%s)", (
                task["task_id"], task["execution_token"], code, "model failure",
            ))
            assert cur.fetchone()[0]["outcome"] == "failed"
            cur.execute("SELECT credits FROM users WHERE id = %s", (task["user_id"],))
            assert cur.fetchone()[0] == 100
            cur.execute("SELECT count(*) FROM credits_history WHERE user_id = %s", (task["user_id"],))
            assert cur.fetchone()[0] == 0
    finally:
        conn.rollback()
        conn.close()

"""迁移 170 的企微 Actor 原子入队权限合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/170_wecom_actor_enqueue_role_grant.sql"
).read_text()
ROLLBACK = (
    ROOT / "migrations/rollback"
    / "170_wecom_actor_enqueue_role_grant_rollback.sql"
).read_text()
SIGNATURE = (
    "enqueue_wecom_generation_turn_v2(\n"
    "    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID\n"
    ")"
)


def test_only_wecom_runtime_receives_seven_argument_enqueue() -> None:
    assert f"REVOKE ALL ON FUNCTION {SIGNATURE}" in SQL
    assert (
        f"GRANT EXECUTE ON FUNCTION {SIGNATURE}\n"
        "TO everydayai_wecom_runtime;"
    ) in SQL


def test_rollback_removes_only_restored_runtime_grant() -> None:
    assert f"REVOKE EXECUTE ON FUNCTION {SIGNATURE}" in ROLLBACK
    assert "FROM everydayai_wecom_runtime;" in ROLLBACK
    assert "DROP FUNCTION" not in ROLLBACK

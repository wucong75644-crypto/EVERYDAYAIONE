from pathlib import Path


ROOT = Path(__file__).parents[1]
SQL = (ROOT / "migrations/227_61_agent_runtime_web_ingress_required.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_61_agent_runtime_web_ingress_required_rollback.sql").read_text()


def test_web_required_ingress_is_separate_and_fail_closed():
    assert "CREATE FUNCTION runtime_submit_ingress_v6_required" in SQL
    assert "runtime_submit_ingress_v5(" in SQL
    assert "runtime_required_unavailable" in SQL
    assert "'actor', FALSE" in SQL
    assert "'runtime_rejected', TRUE" in SQL
    assert "TO everydayai_runtime" in SQL
    assert "FROM PUBLIC, everydayai_worker, everydayai_wecom_runtime" in SQL
    assert "DROP FUNCTION IF EXISTS runtime_submit_ingress_v6_required" in ROLLBACK

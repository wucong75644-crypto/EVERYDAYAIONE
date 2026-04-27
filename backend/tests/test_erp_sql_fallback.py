"""erp_sql_fallback.py 单元测试——SQL 兜底完整流程。"""
import sys
from pathlib import Path

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


ORG = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"


# ── _clean_sql ──


class TestCleanSql:

    def _clean(self, raw):
        from services.kuaimai.erp_sql_fallback import _clean_sql
        return _clean_sql(raw)

    def test_strips_markdown_code_block(self):
        raw = "```sql\nSELECT 1\n```"
        assert self._clean(raw) == "SELECT 1"

    def test_strips_trailing_semicolon(self):
        assert self._clean("SELECT 1;") == "SELECT 1"

    def test_strips_whitespace(self):
        assert self._clean("  SELECT 1  ") == "SELECT 1"

    def test_combined(self):
        raw = "```\nSELECT * FROM t;\n```"
        assert self._clean(raw) == "SELECT * FROM t"

    def test_no_markdown(self):
        assert self._clean("SELECT 1") == "SELECT 1"


# ── build_dynamic_context ──


class TestBuildDynamicContext:

    def _build(self, query="test", summary="failed", params=None):
        from services.kuaimai.erp_sql_fallback import build_dynamic_context
        return build_dynamic_context(query, summary, params, ORG)

    def test_contains_user_query(self):
        ctx = self._build(query="4月退货率")
        assert "4月退货率" in ctx

    def test_contains_failed_summary(self):
        ctx = self._build(summary="RPC 超时")
        assert "RPC 超时" in ctx

    def test_contains_org_id_constraint(self):
        ctx = self._build()
        assert ORG in ctx

    def test_contains_plan_params(self):
        ctx = self._build(params={"doc_type": "order", "metrics": ["return_rate"]})
        assert "return_rate" in ctx

    def test_without_params(self):
        ctx = self._build(params=None)
        assert "org_id" in ctx  # 安全约束仍在


# ── validate_generated_sql（扩展测试）──


class TestValidateExtended:

    def _ok(self, sql):
        from services.kuaimai.erp_sql_fallback import validate_generated_sql
        ok, _ = validate_generated_sql(sql, ORG)
        return ok

    def test_select_star_ok(self):
        assert self._ok(f"SELECT * FROM t WHERE org_id = '{ORG}' LIMIT 10")

    def test_with_cte_ok(self):
        assert self._ok(f"WITH x AS (SELECT 1) SELECT * FROM x WHERE org_id = '{ORG}' LIMIT 10")

    def test_update_rejected(self):
        assert not self._ok(f"UPDATE t SET x=1 WHERE org_id = '{ORG}' LIMIT 10")

    def test_truncate_rejected(self):
        assert not self._ok(f"TRUNCATE t; SELECT 1 WHERE org_id = '{ORG}' LIMIT 10")

    def test_missing_org_id(self):
        assert not self._ok("SELECT 1 LIMIT 10")

    def test_wrong_org_id(self):
        assert not self._ok("SELECT 1 WHERE org_id = 'other-org' LIMIT 10")

    def test_missing_limit(self):
        assert not self._ok(f"SELECT 1 WHERE org_id = '{ORG}'")

    def test_limit_over_1000(self):
        assert not self._ok(f"SELECT 1 WHERE org_id = '{ORG}' LIMIT 5000")

    def test_limit_1000_ok(self):
        assert self._ok(f"SELECT 1 WHERE org_id = '{ORG}' LIMIT 1000")

    def test_grant_rejected(self):
        assert not self._ok(f"GRANT ALL ON t TO public WHERE org_id = '{ORG}' LIMIT 1")


# ── should_try_sql（扩展测试）──


class TestShouldTryExtended:

    def _should(self, status, summary="", error="", meta=None):
        from services.kuaimai.erp_sql_fallback import should_try_sql

        class R:
            pass
        r = R()
        r.status = status
        r.summary = summary
        r.error_message = error
        r.metadata = meta or {}
        return should_try_sql(r, "test")

    def test_error_triggers(self):
        assert self._should("error", "查询失败")

    def test_empty_triggers(self):
        assert self._should("empty")

    def test_success_no_trigger(self):
        assert not self._should("success")

    def test_partial_no_trigger(self):
        assert not self._should("partial")

    def test_timeout_no_trigger(self):
        assert not self._should("error", "查询超时了")
        assert not self._should("timeout")

    def test_param_error_no_trigger(self):
        assert not self._should("error", "参数不合法")

    def test_doc_type_error_no_trigger(self):
        assert not self._should("error", "", "invalid doc_type")

    def test_alert_no_trigger(self):
        assert not self._should("error", "预警失败", meta={"query_type": "alert"})

    def test_cant_understand_no_trigger(self):
        assert not self._should("error", "无法理解您的请求")

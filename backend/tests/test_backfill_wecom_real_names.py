"""
backfill_wecom_real_names.py 脚本核心逻辑测试

只测纯函数 list_placeholder_mappings / resolve_real_names / apply_backfill,
不动主脚本入口（main）。
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
scripts_dir = backend_dir / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from backfill_wecom_real_names import (  # noqa: E402
    list_placeholder_mappings,
    resolve_real_names,
    apply_backfill,
)


def _make_db():
    """构造支持 .table(name) 的复用 mock"""
    db = MagicMock()
    table_mocks: dict[str, MagicMock] = {}

    def _table(name: str):
        if name not in table_mocks:
            table_mocks[name] = MagicMock(name=f"table({name})")
        return table_mocks[name]

    db.table = MagicMock(side_effect=_table)
    db._table_mocks = table_mocks
    return db


# ════════════════════════════════════════════════════════════════
# list_placeholder_mappings
# ════════════════════════════════════════════════════════════════

class TestListPlaceholderMappings:
    def test_returns_placeholder_mappings(self):
        db = _make_db()
        mappings_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        mappings_table.select.return_value.like.return_value.execute.return_value = (
            MagicMock(data=[
                {
                    "user_id": "u1", "wecom_userid": "ww_zhangsan",
                    "corp_id": "corp_a", "org_id": "org-1",
                    "wecom_nickname": "企微用户_ww_zhang",
                },
                {
                    "user_id": "u2", "wecom_userid": "ww_lisi",
                    "corp_id": "corp_a", "org_id": "org-1",
                    "wecom_nickname": "企微用户_ww_lisi_",
                },
            ])
        )

        rows = list_placeholder_mappings(db, org_id=None)
        assert len(rows) == 2
        assert rows[0]["old_nickname"].startswith("企微用户_")
        assert rows[1]["wecom_userid"] == "ww_lisi"

    def test_org_id_filter_applied(self):
        db = _make_db()
        mappings_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        mappings_table.select.return_value.like.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[])
        )

        rows = list_placeholder_mappings(db, org_id="org-only")
        assert rows == []
        mappings_table.select.return_value.like.return_value.eq.assert_called_with("org_id", "org-only")


# ════════════════════════════════════════════════════════════════
# resolve_real_names
# ════════════════════════════════════════════════════════════════

class TestResolveRealNames:
    @pytest.mark.asyncio
    async def test_calls_user_get_per_mapping(self):
        """每条映射调一次 user/get，命中真名进 candidates"""
        db = _make_db()
        mappings = [
            {"user_id": "u1", "wecom_userid": "ww_zhangsan",
             "corp_id": "corp_a", "org_id": "org-1",
             "old_nickname": "企微用户_ww_zhang"},
            {"user_id": "u2", "wecom_userid": "ww_lisi",
             "corp_id": "corp_a", "org_id": "org-1",
             "old_nickname": "企微用户_ww_lisi_"},
        ]

        async def fake_fetch(d, org, uid, **kw):
            return {"ww_zhangsan": "张三", "ww_lisi": "李四"}.get(uid)

        with patch(
            "backfill_wecom_real_names.fetch_wecom_real_name",
            new=fake_fetch,
        ):
            result = await resolve_real_names(db, mappings)

        assert len(result) == 2
        names = {c["new_name"] for c in result}
        assert names == {"张三", "李四"}

    @pytest.mark.asyncio
    async def test_skips_mapping_without_org_id(self):
        """org_id 为空（散客企微用户）→ 跳过，不调 API"""
        db = _make_db()
        mappings = [
            {"user_id": "u1", "wecom_userid": "ww_x",
             "corp_id": "corp_a", "org_id": None,
             "old_nickname": "企微用户_ww_x"},
        ]

        called = {"count": 0}

        async def fake_fetch(*args, **kwargs):
            called["count"] += 1
            return "不应被调用"

        with patch(
            "backfill_wecom_real_names.fetch_wecom_real_name",
            new=fake_fetch,
        ):
            result = await resolve_real_names(db, mappings)

        assert result == []
        assert called["count"] == 0

    @pytest.mark.asyncio
    async def test_skips_when_api_returns_none(self):
        """API 返回 None（不在可见范围）→ 不进 candidates"""
        db = _make_db()
        mappings = [
            {"user_id": "u1", "wecom_userid": "ww_unknown",
             "corp_id": "corp_a", "org_id": "org-1",
             "old_nickname": "企微用户_ww_unkno"},
        ]

        async def fake_fetch(*args, **kwargs):
            return None

        with patch(
            "backfill_wecom_real_names.fetch_wecom_real_name",
            new=fake_fetch,
        ):
            result = await resolve_real_names(db, mappings)

        assert result == []


# ════════════════════════════════════════════════════════════════
# apply_backfill
# ════════════════════════════════════════════════════════════════

class TestApplyBackfill:
    def test_updates_users_and_mappings(self):
        """正常路径：users.nickname 是兜底 → users + mappings 都更新"""
        db = _make_db()

        users_table = db._table_mocks.setdefault("users", MagicMock())
        users_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
            MagicMock(data={"nickname": "企微用户_ww_zhang"})
        )
        users_table.update.return_value.eq.return_value.execute.return_value = MagicMock()

        mappings_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        mappings_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock()

        candidates = [{
            "user_id": "u1", "wecom_userid": "ww_zhangsan",
            "corp_id": "corp_a", "org_id": "org-1",
            "old_nickname": "企微用户_ww_zhang", "new_name": "张三",
        }]
        stats = apply_backfill(db, candidates)

        assert stats["users_updated"] == 1
        assert stats["mappings_updated"] == 1
        assert stats["users_skipped"] == 0
        assert stats["errors"] == 0

    def test_skips_user_with_manual_real_name(self):
        """用户 nickname 已被手动改成真名 → 不要覆盖"""
        db = _make_db()

        users_table = db._table_mocks.setdefault("users", MagicMock())
        users_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
            MagicMock(data={"nickname": "张总（手动改的）"})
        )

        mappings_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        mappings_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock()

        candidates = [{
            "user_id": "u1", "wecom_userid": "ww_zhangsan",
            "corp_id": "corp_a", "org_id": "org-1",
            "old_nickname": "企微用户_ww_zhang", "new_name": "张三",
        }]
        stats = apply_backfill(db, candidates)

        assert stats["users_updated"] == 0
        assert stats["users_skipped"] == 1
        # 但映射表还是要更新（用作机器人识别真名）
        assert stats["mappings_updated"] == 1

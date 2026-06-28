"""WecomDuplicateMonitor 单元测试

覆盖：
- 健康场景：无孤儿、无重复 → 不触发 error log
- 孤儿用户：created_by='wecom' 但无 mapping → error
- 重复账号：同 nickname × 多 → error
- DB 异常：捕获不阻断（返回 0）
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.wecom_dup_monitor import WecomDuplicateMonitor


def _make_chain(data=None):
    c = MagicMock()
    for m in ("select", "eq", "in_"):
        getattr(c, m).return_value = c
    c.execute.return_value = MagicMock(data=data or [])
    return c


class TestWecomDupMonitor:
    @pytest.mark.asyncio
    async def test_healthy_no_alert(self):
        """无孤儿 + 无重复 → 不触发 error"""
        db = MagicMock()
        # users.select(...).eq('created_by','wecom')... 用于 orphan check 和 duplicate check
        users_chain = _make_chain(data=[
            {"id": "u1", "nickname": "张三"},
            {"id": "u2", "nickname": "李四"},
        ])
        mapping_chain = _make_chain(data=[
            {"user_id": "u1"}, {"user_id": "u2"},
        ])

        def _table(name):
            return mapping_chain if name == "wecom_user_mappings" else users_chain

        db.table = MagicMock(side_effect=_table)

        with patch("services.wecom_dup_monitor.logger") as log:
            mon = WecomDuplicateMonitor(db)
            result = await mon.check_and_alert()

            assert result["orphan_users"] == 0
            assert result["duplicate_groups"] == 0
            log.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_users_alert(self):
        """有孤儿用户 → logger.error"""
        db = MagicMock()
        users_chain = _make_chain(data=[
            {"id": "u1", "nickname": "孤儿A"},
            {"id": "u2", "nickname": "孤儿B"},
            {"id": "u3", "nickname": "正常"},
        ])
        mapping_chain = _make_chain(data=[{"user_id": "u3"}])  # 只有 u3 有 mapping

        def _table(name):
            return mapping_chain if name == "wecom_user_mappings" else users_chain

        db.table = MagicMock(side_effect=_table)

        with patch("services.wecom_dup_monitor.logger") as log:
            result = await WecomDuplicateMonitor(db).check_and_alert()
            assert result["orphan_users"] == 2
            log.error.assert_called()
            # 错误消息含关键字
            err_msg = log.error.call_args[0][0]
            assert "orphan" in err_msg.lower()

    @pytest.mark.asyncio
    async def test_duplicate_groups_alert(self):
        """同 nickname × 多 → logger.error，含样本"""
        db = MagicMock()
        users_chain = _make_chain(data=[
            {"id": f"u{i}", "nickname": "廖娟"} for i in range(5)
        ] + [
            {"id": "uA", "nickname": "唯一名"}
        ])
        # 假设全有 mapping，避免触发 orphan 告警混淆
        mapping_chain = _make_chain(data=[
            {"user_id": f"u{i}"} for i in range(5)
        ] + [{"user_id": "uA"}])

        def _table(name):
            return mapping_chain if name == "wecom_user_mappings" else users_chain

        db.table = MagicMock(side_effect=_table)

        with patch("services.wecom_dup_monitor.logger") as log:
            result = await WecomDuplicateMonitor(db).check_and_alert()
            assert result["duplicate_groups"] == 1
            assert result["duplicate_samples"][0]["nickname"] == "廖娟"
            assert result["duplicate_samples"][0]["count"] == 5
            log.error.assert_called()
            err_msg = log.error.call_args[0][0]
            assert "duplicate" in err_msg.lower()
            assert "廖娟" in err_msg

    @pytest.mark.asyncio
    async def test_db_exception_no_raise(self):
        """DB 异常被捕获不抛出，返回 0"""
        db = MagicMock()
        db.table.side_effect = RuntimeError("DB down")

        mon = WecomDuplicateMonitor(db)
        result = await mon.check_and_alert()
        # 两类都返回 0（异常被吞）
        assert result["orphan_users"] == 0
        assert result["duplicate_groups"] == 0

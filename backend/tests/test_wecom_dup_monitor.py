"""WecomDuplicateMonitor 单元测试

覆盖：
- 健康场景：RPC 返回无孤儿、无重复 → 不触发 error log
- 孤儿用户与重复身份 → error
- DB 异常和无效响应：失败关闭，不伪装通过
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.wecom_dup_monitor import WecomDuplicateMonitor


class TestWecomDupMonitor:
    @pytest.mark.asyncio
    async def test_healthy_no_alert(self):
        """无孤儿 + 无重复 → 不触发 error"""
        db = MagicMock()

        with patch("services.wecom_dup_monitor.logger") as log, patch(
            "services.wecom_dup_monitor.asyncio.to_thread",
            new=AsyncMock(return_value=MagicMock(
                data={"orphan_users": 0, "duplicate_groups": 0},
            )),
        ):
            mon = WecomDuplicateMonitor(db)
            result = await mon.check_and_alert()

            assert result["orphan_users"] == 0
            assert result["duplicate_groups"] == 0
            log.error.assert_not_called()
            db.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_users_alert(self):
        """有孤儿用户 → logger.error"""
        db = MagicMock()

        with patch("services.wecom_dup_monitor.logger") as log, patch(
            "services.wecom_dup_monitor.asyncio.to_thread",
            new=AsyncMock(return_value=MagicMock(
                data={"orphan_users": 2, "duplicate_groups": 0},
            )),
        ):
            result = await WecomDuplicateMonitor(db).check_and_alert()
            assert result["orphan_users"] == 2
            log.error.assert_called()
            # 错误消息含关键字
            err_msg = log.error.call_args[0][0]
            assert "orphan" in err_msg.lower()

    @pytest.mark.asyncio
    async def test_duplicate_groups_alert(self):
        """重复企微身份组 → logger.error，不返回身份样本"""
        db = MagicMock()

        with patch("services.wecom_dup_monitor.logger") as log, patch(
            "services.wecom_dup_monitor.asyncio.to_thread",
            new=AsyncMock(return_value=MagicMock(
                data={"orphan_users": 0, "duplicate_groups": 1},
            )),
        ):
            result = await WecomDuplicateMonitor(db).check_and_alert()
            assert result["duplicate_groups"] == 1
            assert result["duplicate_samples"] == []
            log.error.assert_called()
            err_msg = log.error.call_args[0][0]
            assert "duplicate" in err_msg.lower()

    @pytest.mark.asyncio
    async def test_db_exception_raises(self):
        """DB 异常必须上抛，不能转换为健康结果"""
        db = MagicMock()
        with patch(
            "services.wecom_dup_monitor.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            with pytest.raises(RuntimeError, match="DB down"):
                await WecomDuplicateMonitor(db).check_and_alert()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [None, {}, {"orphan_users": -1, "duplicate_groups": 0}],
    )
    async def test_invalid_snapshot_raises(self, payload):
        with patch(
            "services.wecom_dup_monitor.asyncio.to_thread",
            new=AsyncMock(return_value=MagicMock(data=payload)),
        ):
            with pytest.raises(
                RuntimeError,
                match="WECOM_IDENTITY_HEALTH_SNAPSHOT_INVALID",
            ):
                await WecomDuplicateMonitor(MagicMock()).check_and_alert()

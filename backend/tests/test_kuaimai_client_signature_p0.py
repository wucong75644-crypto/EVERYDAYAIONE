"""快麦 API 签名时间戳 P0 修复回归测试。

背景：[client.py:181/192/271](backend/services/kuaimai/client.py#L181) 之前用
``datetime.now().strftime("%Y-%m-%d %H:%M:%S")`` 生成签名时间戳，
依赖 OS 时区。如服务器 TZ 漂移会导致所有快麦 API 请求被签名校验拒绝，
整个 ERP 同步直接挂掉。

修复：改用 ``utils.time_context.now_cn()``（ZoneInfo("Asia/Shanghai") aware）。

设计文档：docs/document/TECH_ERP时间准确性架构.md §17 N1
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


CN = ZoneInfo("Asia/Shanghai")


def test_client_uses_now_cn_not_naive_datetime():
    """client.py 必须 import now_cn，且不再使用 datetime.now()."""
    import services.kuaimai.client as client_mod
    src = open(client_mod.__file__).read()

    # 必须 import now_cn
    assert "from utils.time_context import now_cn" in src

    # 不能再有裸 datetime.now() 调用
    assert "datetime.now()" not in src, (
        "client.py 仍存在 datetime.now() 裸调用！会导致签名时区漂移"
    )

    # 必须用 now_cn() 生成签名时间戳
    assert "now_cn().strftime" in src


def test_now_cn_returns_aware_china_time():
    """now_cn 必须返回 aware datetime + +08:00 偏移。"""
    from utils.time_context import now_cn
    n = now_cn()
    assert n.tzinfo is not None
    # +08:00 偏移
    assert n.utcoffset().total_seconds() == 8 * 3600


def test_signature_timestamp_format_matches_kuaimai_spec():
    """快麦 API 要求：YYYY-MM-DD HH:MM:SS 格式，北京时间。"""
    from utils.time_context import now_cn
    ts = now_cn().strftime("%Y-%m-%d %H:%M:%S")
    # 19 个字符
    assert len(ts) == 19
    # 格式校验：YYYY-MM-DD HH:MM:SS
    parsed = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    # 必须是当前时间附近（5 秒内）
    diff = abs((datetime.now(CN).replace(tzinfo=None) - parsed).total_seconds())
    assert diff < 5, f"时间戳偏离: {diff}s"


def test_signature_unaffected_by_os_tz_drift():
    """即使 OS TZ 是 UTC，now_cn() 仍返回北京时间。

    这是 P0 修复的核心 — 服务器 TZ 漂移不能影响签名。
    """
    from utils.time_context import now_cn
    old_tz = os.environ.get("TZ")
    try:
        # 模拟服务器 TZ=UTC（生产事故场景）
        os.environ["TZ"] = "UTC"
        # 注意：tzset 在 macOS 不一定有效，但 ZoneInfo 不依赖 OS TZ
        try:
            import time as _t
            _t.tzset()
        except (ImportError, AttributeError):
            pass

        # now_cn 仍然必须返回北京时间
        n = now_cn()
        assert n.tzinfo is not None
        assert n.utcoffset().total_seconds() == 8 * 3600
    finally:
        if old_tz is not None:
            os.environ["TZ"] = old_tz
        else:
            os.environ.pop("TZ", None)
        try:
            import time as _t
            _t.tzset()
        except (ImportError, AttributeError):
            pass

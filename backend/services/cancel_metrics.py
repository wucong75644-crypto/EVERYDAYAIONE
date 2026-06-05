"""Cancel 链路可观测性指标（Phase 2）

设计参考 docs/document/TECH_用户中断与恢复机制.md §十二

v1 实现：纯 log 输出，先把指标数据落到 stdout，等观测后端接入（Datadog / OTel）时
再切换 sink。命名遵循 OpenTelemetry GenAI 规范前缀 `gen_ai.cancel.*`。

四个核心指标：
- gen_ai.cancel.events       Counter   每次取消事件
- gen_ai.cancel.latency      Histogram 点击→真停延迟 (ms)
- gen_ai.cancel.orphan_fixed Counter   history_loader 兜底命中次数
- gen_ai.cancel.continued_5m Counter   取消后 5 分钟内点继续

Tags：org_id / phase / had_partial / tools_in_flight / cancel_source
"""

import time
from typing import Dict

from loguru import logger


_cancel_started_at: Dict[str, float] = {}


def mark_cancel_start(task_id: str) -> None:
    """取消触发瞬间调用，记录起始时刻。

    被 api/routes/task.py 取消路径调用，与 cancel_task 一同触发。
    """
    _cancel_started_at[task_id] = time.time()


def record_cancel_event(
    task_id: str,
    org_id: str | None = None,
    had_partial: bool = False,
    tools_in_flight: int = 0,
    cancel_source: str = "frontend_button",
) -> None:
    """gen_ai.cancel.events — 取消事件计数。"""
    logger.info(
        f"metric=gen_ai.cancel.events | task={task_id} | org={org_id} | "
        f"had_partial={had_partial} | tools_in_flight={tools_in_flight} | "
        f"source={cancel_source}"
    )


def record_cancel_latency(
    task_id: str,
    org_id: str | None = None,
    phase: str = "stream",
    had_partial: bool = False,
    tools_in_flight: int = 0,
) -> None:
    """gen_ai.cancel.latency — 点击→真停延迟 Histogram。

    依赖 mark_cancel_start 已写入起始时刻。若未找到则跳过（说明 cancel 路径异常）。
    """
    started_at = _cancel_started_at.pop(task_id, None)
    if started_at is None:
        return

    latency_ms = int((time.time() - started_at) * 1000)
    logger.info(
        f"metric=gen_ai.cancel.latency | task={task_id} | org={org_id} | "
        f"phase={phase} | latency_ms={latency_ms} | "
        f"had_partial={had_partial} | tools_in_flight={tools_in_flight}"
    )


def record_orphan_fixed(
    task_id: str,
    org_id: str | None = None,
    fixed_count: int = 0,
) -> None:
    """gen_ai.cancel.orphan_fixed — history_loader 兜底命中次数。"""
    if fixed_count <= 0:
        return
    logger.info(
        f"metric=gen_ai.cancel.orphan_fixed | task={task_id} | org={org_id} | "
        f"fixed_count={fixed_count}"
    )


def record_continued_5m(
    task_id: str,
    org_id: str | None = None,
) -> None:
    """gen_ai.cancel.continued_5m — 取消后 5 分钟内点继续。"""
    logger.info(
        f"metric=gen_ai.cancel.continued_5m | task={task_id} | org={org_id}"
    )

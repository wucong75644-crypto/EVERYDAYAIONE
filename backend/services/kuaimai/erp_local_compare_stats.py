"""
ERP 本地同比/环比对比工具

替代 LLM 临场组合两次 local_global_stats 的做法 — 由后端确定地计算对比基线，
返回结构化时间块 + 数据对比，避免 LLM 自行推算 weekday 或对比日期。

所有函数返回 ToolOutput（Phase 0 改造）。

设计文档:
- docs/document/TECH_ERP时间准确性架构.md §6.2.3 (B6)
- §14 神经-符号分离原则
重构文档: docs/document/TECH_多Agent单一职责重构.md §4.3
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timedelta
from typing import Any, Optional

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_local_helpers import CN_TZ, check_sync_health
from utils.time_context import (
    ComparePoint,
    DateRange,
    RequestContext,
    format_time_header,
)

_DOC_TYPE_NAMES = {
    "purchase": "采购",
    "receipt": "收货",
    "shelf": "上架",
    "order": "订单",
    "aftersale": "售后",
    "purchase_return": "采退",
}

_VALID_TIME_TYPES = {"doc_created_at", "pay_time", "consign_time"}

_TIME_TYPE_LABELS = {
    "doc_created_at": "下单",
    "pay_time": "付款",
    "consign_time": "发货",
}


# ────────────────────────────────────────────────────────────────────
# 当前期 / 基线期 计算
# ────────────────────────────────────────────────────────────────────


def _current_range(
    ctx: RequestContext, current_period: str, current_n: Optional[int],
    current_start: Optional[str], current_end: Optional[str],
) -> DateRange:
    """根据 current_period 枚举构造当前期 DateRange。"""
    if current_period == "today":
        return DateRange.for_today(ctx)
    if current_period == "yesterday":
        return DateRange.for_yesterday(ctx)
    if current_period == "this_week":
        return DateRange.for_this_week(ctx)
    if current_period == "this_month":
        return DateRange.for_this_month(ctx)
    if current_period == "last_n_days":
        if not current_n or current_n < 1:
            raise ValueError("current_period=last_n_days 时必须传 current_n >= 1")
        return DateRange.for_last_n_days(ctx, current_n)
    if current_period == "custom":
        if not (current_start and current_end):
            raise ValueError(
                "current_period=custom 时必须同时传 current_start 和 current_end",
            )
        s = _parse_iso(current_start)
        e = _parse_iso(current_end, is_end=True)
        return DateRange.custom(s, e, reference=ctx.now)
    raise ValueError(f"未知 current_period: {current_period}")


def _baseline_range(
    ctx: RequestContext, current: DateRange, compare_kind: str,
    baseline_start: Optional[str], baseline_end: Optional[str],
) -> DateRange:
    """根据 compare_kind 推导基线期 DateRange。

    支持的 compare_kind：
        - wow: 周环比，把当前期整体往前推 7 天
        - mom: 月环比，把当前期整体往前推 1 个月（同位置）
        - yoy: 年同比，往前推 1 年（同月同日）
        - spring_aligned: 春节对齐（暂用 yoy 同等处理，未来可替换）
        - custom: 用 baseline_start/baseline_end
    """
    cur_s = datetime.fromisoformat(current.start.iso)
    cur_e = datetime.fromisoformat(current.end.iso)

    if compare_kind == "wow":
        b_s = cur_s - timedelta(days=7)
        b_e = cur_e - timedelta(days=7)
    elif compare_kind == "mom":
        b_s = _shift_months(cur_s, -1)
        b_e = _shift_months(cur_e, -1)
    elif compare_kind in ("yoy", "spring_aligned"):
        # 公历同比 — 往前推 1 年。02-29 在非闰年降级到 02-28。
        # spring_aligned 暂时按 yoy 处理（未来若需启用真正的春节对齐，
        # 可在 utils/holiday.py 加 align_to_spring_festival 函数后替换此分支）
        b_s = _shift_years(cur_s, -1)
        b_e = _shift_years(cur_e, -1)
    elif compare_kind == "custom":
        if not (baseline_start and baseline_end):
            raise ValueError(
                "compare_kind=custom 时必须同时传 baseline_start 和 baseline_end",
            )
        b_s = _parse_iso(baseline_start)
        b_e = _parse_iso(baseline_end, is_end=True)
    else:
        raise ValueError(f"未知 compare_kind: {compare_kind}")

    return DateRange.custom(b_s, b_e, reference=ctx.now)


def _shift_months(dt: datetime, months: int) -> datetime:
    """安全地往前/后推 N 个月（处理月末日不存在的情况）。"""
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    # 月末日处理：如果原日不存在于新月份，降级到新月份的最后一天
    day = min(dt.day, _last_day_of_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def _shift_years(dt: datetime, years: int) -> datetime:
    """安全地往前/后推 N 年（处理 02-29 的情况）。"""
    new_year = dt.year + years
    try:
        return dt.replace(year=new_year)
    except ValueError:
        # 02-29 → 非闰年 02-28
        return dt.replace(year=new_year, day=28)


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def _parse_iso(s: str, *, is_end: bool = False) -> datetime:
    """解析 ISO 8601 / YYYY-MM-DD HH:MM 字符串为 CN_TZ aware datetime。"""
    normalized = s.strip().replace(" ", "T")
    dt = datetime.fromisoformat(normalized)
    if is_end and "T" not in normalized and dt.hour == 0:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=CN_TZ) if dt.tzinfo is None else dt.astimezone(CN_TZ)


# ────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────


_COMPARE_COLUMNS = [
    ColumnMeta("period", "text", "期间"),
    ColumnMeta("doc_count", "integer", "单数"),
    ColumnMeta("total_qty", "integer", "数量"),
    ColumnMeta("total_amount", "numeric", "金额"),
]


async def local_compare_stats(
    db,
    doc_type: str,
    compare_kind: str,
    current_period: str,
    current_n: Optional[int] = None,
    current_start: Optional[str] = None,
    current_end: Optional[str] = None,
    baseline_start: Optional[str] = None,
    baseline_end: Optional[str] = None,
    time_type: Optional[str] = None,
    shop_name: Optional[str] = None,
    platform: Optional[str] = None,
    supplier_name: Optional[str] = None,
    warehouse_name: Optional[str] = None,
    rank_by: Optional[str] = None,
    group_by: Optional[str] = None,
    org_id: Optional[str] = None,
    request_ctx: Optional[RequestContext] = None,
) -> ToolOutput:
    """同比/环比对比统计（时间事实层）。

    不要调 local_global_stats 两次拼对比 — 必须用本工具，由后端：
    1) 确定地计算当前期/基线期 DateRange（自带 weekday_cn / iso_week / relative_label）
    2) 复用 erp_global_stats_query RPC 双查
    3) 返回结构化时间块 + 对比数据
    """
    # ── 1. 构造 RequestContext / current / baseline ────
    ctx = request_ctx or RequestContext.build(
        user_id="anonymous", org_id=org_id,
    )

    try:
        current = _current_range(
            ctx, current_period, current_n, current_start, current_end,
        )
        baseline = _baseline_range(
            ctx, current, compare_kind, baseline_start, baseline_end,
        )
    except ValueError as e:
        return ToolOutput(
            summary=f"参数错误: {e}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(e),
        )

    cp = ComparePoint.build(
        current=current, baseline=baseline, compare_kind=compare_kind,  # type: ignore[arg-type]
    )

    # ── 2. 校验 time_type / 准备 RPC 参数 ─────────────
    time_col = time_type if time_type in _VALID_TIME_TYPES else "doc_created_at"
    rpc_group = group_by or ("product" if rank_by else None)
    type_name = _DOC_TYPE_NAMES.get(doc_type, doc_type)

    base_params = {
        "p_doc_type": doc_type,
        "p_time_col": time_col,
        "p_shop": shop_name or None,
        "p_platform": platform or None,
        "p_supplier": supplier_name or None,
        "p_warehouse": warehouse_name or None,
        "p_group_by": rpc_group,
        "p_limit": 20,
        "p_org_id": org_id,
        "p_filters": None,  # 预留：未来支持传入 DSL 过滤器
    }

    # ── 3. 双查 ────────────────────────────────────────
    # 订单类型走分类引擎（过滤空包/刷单/补发/已关闭），其他类型走原始汇总
    if doc_type == "order" and rpc_group is None:
        classified = _classified_compare(
            db, org_id, current, baseline, time_col,
            shop_name, platform, supplier_name, warehouse_name,
        )
        if classified is not None:
            cur_data, base_data = classified
        else:
            cur_data, base_data = _raw_compare(db, base_params, current, baseline)
    else:
        cur_data, base_data = _raw_compare(db, base_params, current, baseline)

    if isinstance(cur_data, ToolOutput):
        return cur_data  # error output

    if isinstance(cur_data, dict) and "error" in cur_data:
        return ToolOutput(
            summary=f"查询参数错误: {cur_data['error']}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(cur_data["error"]),
        )
    if isinstance(base_data, dict) and "error" in base_data:
        return ToolOutput(
            summary=f"查询参数错误: {base_data['error']}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(base_data["error"]),
        )

    # ── 4. 渲染结构化输出 ──────────────────────────────
    return _render_compare_output(
        cp=cp, cur_data=cur_data, base_data=base_data,
        type_name=type_name, time_col=time_col,
        db=db, doc_type=doc_type, org_id=org_id, request_ctx=ctx,
    )


def _raw_compare(
    db, base_params: dict, current: DateRange, baseline: DateRange,
) -> tuple[Any, Any]:
    """原始 RPC 双查（非订单类型或分类引擎不可用时的回退）。"""
    try:
        cur_data = db.rpc("erp_global_stats_query", {
            **base_params,
            "p_start": current.start.iso,
            "p_end": current.end.iso,
        }).execute().data
        base_data = db.rpc("erp_global_stats_query", {
            **base_params,
            "p_start": baseline.start.iso,
            "p_end": baseline.end.iso,
        }).execute().data
    except Exception as e:
        logger.error(f"local_compare_stats RPC failed | error={e}", exc_info=True)
        err = ToolOutput(
            summary=f"对比查询失败: {e}",
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message=str(e),
        )
        return err, err
    return cur_data, base_data


def _classified_compare(
    db,
    org_id: Optional[str],
    current: DateRange,
    baseline: DateRange,
    time_col: str,
    shop_name: Optional[str],
    platform: Optional[str],
    supplier_name: Optional[str],
    warehouse_name: Optional[str],
) -> tuple[dict, dict] | None:
    """订单分类对比：走 erp_order_stats_grouped + OrderClassifier，返回有效订单数据。

    失败时返回 None，调用方回退到 _raw_compare。
    """
    from services.kuaimai.order_classifier import OrderClassifier

    # 构建 DSL 过滤器（erp_order_stats_grouped 只接受 p_filters）
    dsl: list[dict] = []
    if shop_name:
        dsl.append({"field": "shop_name", "op": "like", "value": f"%{shop_name}%"})
    if platform:
        dsl.append({"field": "platform", "op": "eq", "value": platform})
    if supplier_name:
        dsl.append({"field": "supplier_name", "op": "like", "value": f"%{supplier_name}%"})
    if warehouse_name:
        dsl.append({"field": "warehouse_name", "op": "like", "value": f"%{warehouse_name}%"})

    try:
        classifier = OrderClassifier.for_org(db, org_id)
    except Exception as e:
        logger.warning(f"分类引擎加载异常，回退原逻辑 | error={e}")
        return None

    params_base = {
        "p_org_id": org_id,
        "p_time_col": time_col,
        "p_filters": _json.dumps(dsl) if dsl else None,
        "p_group_by": None,
    }

    try:
        cur_rows = db.rpc("erp_order_stats_grouped", {
            **params_base,
            "p_start": current.start.iso,
            "p_end": current.end.iso,
        }).execute().data
        base_rows = db.rpc("erp_order_stats_grouped", {
            **params_base,
            "p_start": baseline.start.iso,
            "p_end": baseline.end.iso,
        }).execute().data
    except Exception as e:
        logger.warning(f"分类统计 RPC 失败，回退原逻辑 | error={e}")
        return None

    def _extract_valid(rows: list[dict] | None) -> dict:
        if not rows:
            return {"doc_count": 0, "total_qty": 0, "total_amount": 0}
        cr = classifier.classify(rows)
        return {
            "doc_count": cr.valid.get("doc_count", 0),
            "total_qty": cr.valid.get("total_qty", 0),
            "total_amount": cr.valid.get("total_amount", 0),
        }

    return _extract_valid(cur_rows), _extract_valid(base_rows)


def _render_compare_output(
    *,
    cp: ComparePoint,
    cur_data,
    base_data,
    type_name: str,
    time_col: str,
    db,
    doc_type: str,
    org_id: Optional[str],
    request_ctx: RequestContext,
) -> ToolOutput:
    """渲染同比/环比对比结果（带结构化时间块）。"""
    # 时间事实块（双时间块 + 语义说明）
    cur_header = format_time_header(
        ctx=request_ctx, range_=cp.current, kind="当前期",
    )
    base_header = format_time_header(
        ctx=request_ctx, range_=cp.baseline, kind="基线期",
    )
    semantic = (
        f"[对比模式] {cp.compare_label}（{cp.compare_kind}） · 语义：{cp.semantic_note}"
    )

    time_label_cn = ""
    if time_col != "doc_created_at":
        time_label_cn = f"（按{_TIME_TYPE_LABELS.get(time_col, time_col)}时间）"

    # 数据对比 — 仅支持 summary 模式（rank_by/group_by 留作后续扩展）
    if not isinstance(cur_data, dict) or not isinstance(base_data, dict):
        body = (
            "对比工具暂不支持 rank_by/group_by 模式，"
            "请用 group_by=None + rank_by=None 重试，"
            "或改用 local_global_stats 两次查询。"
        )
        return ToolOutput(
            summary=_join_blocks(cur_header, base_header, semantic, body),
            source="warehouse",
            status=OutputStatus.ERROR,
            error_message="rank_by/group_by 模式暂不支持",
        )

    cur_count = cur_data.get("doc_count", 0) if cur_data else 0
    cur_qty = cur_data.get("total_qty", 0) if cur_data else 0
    cur_amt = float(cur_data.get("total_amount", 0) if cur_data else 0)

    base_count = base_data.get("doc_count", 0) if base_data else 0
    base_qty = base_data.get("total_qty", 0) if base_data else 0
    base_amt = float(base_data.get("total_amount", 0) if base_data else 0)

    def _delta(cur, base):
        """格式化对比差值。

        ``{:+}`` 格式已经自带正负号，**禁止额外拼接 sign**（避免双加号 bug）。
        """
        diff = cur - base
        if base == 0:
            pct = "N/A" if cur == 0 else "+∞%"
        else:
            pct_val = diff / base * 100
            pct = f"{pct_val:+.1f}%"
        if isinstance(diff, int):
            return f"{diff:+}（{pct}）"
        return f"{diff:+,.2f}（{pct}）"

    body_lines = [
        f"{type_name}对比{time_label_cn}：",
        "",
        f"  单数：{cur_count} 笔（当前期） vs {base_count} 笔（基线期） → {_delta(cur_count, base_count)}",
        f"  数量：{cur_qty} 件（当前期） vs {base_qty} 件（基线期） → {_delta(cur_qty, base_qty)}",
        f"  金额：¥{cur_amt:,.2f}（当前期） vs ¥{base_amt:,.2f}（基线期） → {_delta(cur_amt, base_amt)}",
    ]

    health = check_sync_health(db, [doc_type], org_id=org_id)
    if health:
        body_lines.append("")
        body_lines.append(health)

    body = "\n".join(body_lines)
    summary = _join_blocks(cur_header, base_header, semantic, body)

    # 结构化数据：当前期 vs 基线期
    compare_data = [
        {"period": "current", "doc_count": cur_count, "total_qty": cur_qty, "total_amount": cur_amt},
        {"period": "baseline", "doc_count": base_count, "total_qty": base_qty, "total_amount": base_amt},
    ]

    return ToolOutput(
        summary=summary,
        format=OutputFormat.TABLE,
        source="warehouse",
        columns=_COMPARE_COLUMNS,
        data=compare_data,
        metadata={
            "doc_type": doc_type,
            "compare_kind": cp.compare_kind,
            "time_column": time_col,
        },
    )


def _join_blocks(*blocks: str) -> str:
    return "\n".join(b for b in blocks if b)

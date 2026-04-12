"""系统错误监控 API — 管理面板查看/分析/处理错误日志

权限：仅 super_admin 可访问。
注意：Database 依赖注入的是同步 LocalDBClient，所有 DB 操作不加 await。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.deps import CurrentUserId, Database

router = APIRouter(prefix="/error-monitor", tags=["error-monitor"])


# ── 权限校验 ─────────────────────────────────────────────


def _require_super_admin(user_id: str, db) -> None:
    """仅 super_admin 可访问"""
    result = db.table("users").select("role").eq("id", user_id).maybe_single().execute()
    if not result or not result.data or result.data.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="仅超级管理员可访问")


# ── 请求/响应模型 ────────────────────────────────────────


class ErrorLogItem(BaseModel):
    id: int
    fingerprint: str
    level: str
    module: Optional[str] = None
    function: Optional[str] = None
    line: Optional[int] = None
    message: str
    traceback: Optional[str] = None
    occurrence_count: int
    first_seen_at: str
    last_seen_at: str
    org_id: Optional[str] = None
    is_critical: bool
    is_resolved: bool
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None


class ErrorListResponse(BaseModel):
    items: list[ErrorLogItem]
    total: int
    page: int
    page_size: int


class ErrorStatsResponse(BaseModel):
    today_total: int
    today_critical: int
    week_total: int
    unresolved: int
    top_modules: list[dict]


class SummarizeResponse(BaseModel):
    summary: str


# ── API 端点 ─────────────────────────────────────────────


@router.get("/list", response_model=ErrorListResponse, summary="错误日志列表")
async def list_errors(
    user_id: CurrentUserId,
    db: Database,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    level: Optional[str] = Query(None, description="ERROR 或 CRITICAL"),
    is_critical: Optional[bool] = Query(None),
    is_resolved: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="搜索消息内容"),
    days: int = Query(7, ge=1, le=30, description="最近N天"),
) -> ErrorListResponse:
    _require_super_admin(user_id, db)

    tz = ZoneInfo("Asia/Shanghai")
    since = (datetime.now(tz) - timedelta(days=days)).isoformat()

    query = (
        db.table("error_logs")
        .select("*", count="exact")
        .gte("last_seen_at", since)
        .order("last_seen_at", desc=True)
    )

    if level:
        query = query.eq("level", level.upper())
    if is_critical is not None:
        query = query.eq("is_critical", is_critical)
    if is_resolved is not None:
        query = query.eq("is_resolved", is_resolved)
    if search:
        safe_search = search.replace("%", "\\%").replace("_", "\\_")
        query = query.ilike("message", f"%{safe_search}%")

    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = query.execute()
    items = result.data or []
    total = result.count if hasattr(result, "count") and result.count is not None else len(items)

    return ErrorListResponse(
        items=[ErrorLogItem(**_serialize_row(r)) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=ErrorStatsResponse, summary="错误统计摘要")
async def get_stats(
    user_id: CurrentUserId,
    db: Database,
) -> ErrorStatsResponse:
    _require_super_admin(user_id, db)

    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now - timedelta(days=7)).isoformat()

    today_result = (
        db.table("error_logs")
        .select("id", count="exact")
        .gte("last_seen_at", today_start)
        .execute()
    )
    today_total = today_result.count if hasattr(today_result, "count") and today_result.count else 0

    today_critical_result = (
        db.table("error_logs")
        .select("id", count="exact")
        .gte("last_seen_at", today_start)
        .eq("is_critical", True)
        .execute()
    )
    today_critical = today_critical_result.count if hasattr(today_critical_result, "count") and today_critical_result.count else 0

    week_result = (
        db.table("error_logs")
        .select("id", count="exact")
        .gte("last_seen_at", week_start)
        .execute()
    )
    week_total = week_result.count if hasattr(week_result, "count") and week_result.count else 0

    unresolved_result = (
        db.table("error_logs")
        .select("id", count="exact")
        .eq("is_resolved", False)
        .execute()
    )
    unresolved = unresolved_result.count if hasattr(unresolved_result, "count") and unresolved_result.count else 0

    top_modules_result = (
        db.table("error_logs")
        .select("module, occurrence_count")
        .gte("last_seen_at", week_start)
        .order("occurrence_count", desc=True)
        .limit(10)
        .execute()
    )
    module_counts: dict[str, int] = {}
    for r in (top_modules_result.data or []):
        mod = r.get("module") or "unknown"
        module_counts[mod] = module_counts.get(mod, 0) + (r.get("occurrence_count") or 1)
    top_modules = [
        {"module": k, "count": v}
        for k, v in sorted(module_counts.items(), key=lambda x: -x[1])[:10]
    ]

    return ErrorStatsResponse(
        today_total=today_total,
        today_critical=today_critical,
        week_total=week_total,
        unresolved=unresolved,
        top_modules=top_modules,
    )


@router.post("/summarize", response_model=SummarizeResponse, summary="AI 总结错误趋势")
async def summarize_errors(
    user_id: CurrentUserId,
    db: Database,
    days: int = Query(7, ge=1, le=30),
) -> SummarizeResponse:
    _require_super_admin(user_id, db)

    tz = ZoneInfo("Asia/Shanghai")
    since = (datetime.now(tz) - timedelta(days=days)).isoformat()

    result = (
        db.table("error_logs")
        .select("level, module, function, message, occurrence_count, is_critical, first_seen_at, last_seen_at")
        .gte("last_seen_at", since)
        .order("occurrence_count", desc=True)
        .limit(100)
        .execute()
    )
    errors = result.data or []

    if not errors:
        return SummarizeResponse(summary=f"最近 {days} 天没有错误记录。")

    error_text = "\n".join(
        f"- [{r['level']}] {r['module']}:{r['function']} | "
        f"次数={r['occurrence_count']} | "
        f"{'致命' if r['is_critical'] else '普通'} | "
        f"{r['message'][:150]}"
        for r in errors[:50]
    )

    prompt = (
        f"你是一个服务器运维专家。以下是最近 {days} 天的后端错误日志摘要（共 {len(errors)} 条）：\n\n"
        f"{error_text}\n\n"
        "请用中文分析：\n"
        "1. 主要错误类别和趋势\n"
        "2. 需要优先处理的问题\n"
        "3. 可能的根因和建议\n\n"
        "回复控制在 300 字以内，用 markdown 格式。"
    )

    summary = await _call_ai_summary(prompt)
    return SummarizeResponse(summary=summary)


@router.post("/{error_id}/resolve", summary="标记错误已处理")
async def resolve_error(
    error_id: int,
    user_id: CurrentUserId,
    db: Database,
) -> dict:
    _require_super_admin(user_id, db)

    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz).isoformat()

    result = (
        db.table("error_logs")
        .update({
            "is_resolved": True,
            "resolved_at": now,
            "resolved_by": user_id,
        })
        .eq("id", error_id)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="错误记录不存在")

    return {"success": True, "message": "已标记为已处理"}


@router.delete("/clear", summary="批量清除错误日志")
async def clear_errors(
    user_id: CurrentUserId,
    db: Database,
    before_date: Optional[str] = Query(None, description="清除此日期前的 (YYYY-MM-DD)"),
    resolved_only: bool = Query(True, description="是否只清除已处理的"),
) -> dict:
    _require_super_admin(user_id, db)

    query = db.table("error_logs").delete()

    if resolved_only:
        query = query.eq("is_resolved", True)

    if before_date:
        query = query.lt("last_seen_at", before_date)
    else:
        tz = ZoneInfo("Asia/Shanghai")
        cutoff = (datetime.now(tz) - timedelta(days=7)).isoformat()
        query = query.lt("last_seen_at", cutoff)

    result = query.execute()
    deleted = len(result.data) if result.data else 0

    return {"success": True, "deleted": deleted}


# ── 内部工具函数 ──────────────────────────────────────────


def _serialize_row(row: dict) -> dict:
    """将 DB 行转为可序列化的 dict"""
    for key in ("first_seen_at", "last_seen_at", "resolved_at"):
        if key in row and row[key] is not None:
            row[key] = str(row[key])
    if "org_id" in row and row["org_id"] is not None:
        row["org_id"] = str(row["org_id"])
    return row


async def _call_ai_summary(prompt: str) -> str:
    """调用千问模型生成错误趋势摘要"""
    try:
        from openai import AsyncOpenAI
        from core.config import get_settings

        settings = get_settings()
        client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
        )

        response = await client.chat.completions.create(
            model="qwen-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.3,
            timeout=15,
        )
        return response.choices[0].message.content or "AI 分析失败，请稍后重试"
    except Exception as e:
        logger.warning(f"AI summary failed | {e}")
        return f"AI 分析暂时不可用：{str(e)[:100]}"

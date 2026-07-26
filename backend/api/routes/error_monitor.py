"""系统错误监控 API — 管理面板查看/分析/处理错误日志

权限由数据库能力函数基于当前 Runtime Actor 校验，仅 super_admin 可访问。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.deps import CurrentUserId, Database

router = APIRouter(prefix="/error-monitor", tags=["error-monitor"])


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
    payload = _rpc_data(db, "list_platform_error_logs", {
        "p_page": page,
        "p_page_size": page_size,
        "p_level": level,
        "p_is_critical": is_critical,
        "p_is_resolved": is_resolved,
        "p_search": search,
        "p_days": days,
    })
    items = payload.get("items")
    total = payload.get("total")
    if not isinstance(items, list) or not isinstance(total, int):
        raise HTTPException(status_code=500, detail="错误日志查询结果无效")

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
    payload = _rpc_data(db, "get_platform_error_stats", {})
    return ErrorStatsResponse(**payload)


@router.post("/summarize", response_model=SummarizeResponse, summary="AI 总结错误趋势")
async def summarize_errors(
    user_id: CurrentUserId,
    db: Database,
    days: int = Query(7, ge=1, le=30),
) -> SummarizeResponse:
    errors = _rpc_data(db, "list_platform_error_summary", {"p_days": days})
    if not isinstance(errors, list):
        raise HTTPException(status_code=500, detail="错误摘要查询结果无效")

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
    payload = _rpc_data(
        db, "resolve_platform_error", {"p_error_id": error_id},
    )
    if payload.get("updated") != 1:
        raise HTTPException(status_code=404, detail="错误记录不存在")

    return {"success": True, "message": "已标记为已处理"}


@router.delete("/clear", summary="批量清除错误日志")
async def clear_errors(
    user_id: CurrentUserId,
    db: Database,
    before_date: Optional[date] = Query(None, description="清除此日期前的 (YYYY-MM-DD)"),
    resolved_only: bool = Query(True, description="是否只清除已处理的"),
) -> dict:
    payload = _rpc_data(db, "clear_platform_errors", {
        "p_before_date": before_date.isoformat() if before_date else None,
        "p_resolved_only": resolved_only,
    })
    deleted = payload.get("deleted")
    if not isinstance(deleted, int):
        raise HTTPException(status_code=500, detail="错误日志清理结果无效")
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


def _rpc_data(db: Any, name: str, params: dict[str, Any]) -> Any:
    """执行平台能力并规范化单行 JSONB 返回值。"""
    try:
        payload = db.rpc(name, params).execute().data
    except Exception as exc:
        if "PLATFORM_ADMIN_REQUIRED" in str(exc):
            raise HTTPException(status_code=403, detail="仅超级管理员可访问") from exc
        raise
    if payload is None:
        raise HTTPException(status_code=500, detail="错误监控能力未返回结果")
    return payload


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

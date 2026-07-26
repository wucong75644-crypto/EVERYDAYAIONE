"""知识库指标记录（独立模块，零 LLM 成本）"""

import json
from typing import Any, Dict, Optional

from loguru import logger

from core.db_scope import (
    DatabaseAccessKind,
    database_scope_from_client,
)
from services.knowledge_config import get_pg_connection, is_kb_available


async def record_metric(
    *,
    task_type: str,
    model_id: str,
    status: str,
    error_code: Optional[str] = None,
    cost_time_ms: Optional[int] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    prompt_category: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    retried: bool = False,
    retry_from_model: Optional[str] = None,
    user_id: Optional[str] = None,
    org_id: Optional[str] = None,
    task_id: Optional[str] = None,
    db_source: Any = None,
) -> None:
    """记录任务执行指标（fire-and-forget，不抛异常）"""
    if not is_kb_available():
        return

    database_scope = database_scope_from_client(db_source)
    if database_scope is not None:
        if org_id is not None and org_id != database_scope.org_id:
            raise ValueError("KNOWLEDGE_METRIC_ORG_SCOPE_MISMATCH")
        org_id = database_scope.org_id
        if database_scope.access_kind == DatabaseAccessKind.RUNTIME:
            if database_scope.actor_user_id is None:
                raise ValueError("KNOWLEDGE_METRIC_ACTOR_SCOPE_REQUIRED")
            user_id = database_scope.actor_user_id

    if task_id and db_source is not None:
        try:
            result = await db_source.rpc("worker_record_media_metric", {
                "p_task_id": task_id,
                "p_status": status,
                "p_error_code": error_code,
                "p_cost_time_ms": cost_time_ms,
                "p_prompt_tokens": prompt_tokens,
                "p_completion_tokens": completion_tokens,
                "p_prompt_category": prompt_category,
                "p_params": params or {},
                "p_retried": retried,
                "p_retry_from_model": retry_from_model,
            }).execute()
            payload = result.data if result else None
            if not isinstance(payload, dict) or payload.get("outcome") != "recorded":
                raise RuntimeError("MEDIA_METRIC_RESULT_INVALID")
        except Exception as e:
            logger.warning(
                f"Knowledge metric record failed | model={model_id} | error={e}"
            )
        return

    conn_ctx = await get_pg_connection(db_source)
    if conn_ctx is None:
        return

    try:
        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO knowledge_metrics (
                        task_type, model_id, status, error_code, cost_time_ms,
                        prompt_tokens, completion_tokens, prompt_category,
                        params, retried, retry_from_model, user_id, org_id
                    ) VALUES (
                        %(task_type)s, %(model_id)s, %(status)s, %(error_code)s,
                        %(cost_time_ms)s, %(prompt_tokens)s, %(completion_tokens)s,
                        %(prompt_category)s, %(params)s, %(retried)s,
                        %(retry_from_model)s, %(user_id)s, %(org_id)s
                    );
                    """,
                    {
                        "task_type": task_type,
                        "model_id": model_id,
                        "status": status,
                        "error_code": error_code,
                        "cost_time_ms": cost_time_ms,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "prompt_category": prompt_category,
                        "params": json.dumps(params or {}),
                        "retried": retried,
                        "retry_from_model": retry_from_model,
                        "user_id": user_id,
                        "org_id": org_id,
                    },
                )
    except Exception as e:
        logger.warning(f"Knowledge metric record failed | model={model_id} | error={e}")

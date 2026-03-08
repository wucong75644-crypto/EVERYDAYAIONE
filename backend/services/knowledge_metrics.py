"""知识库指标记录（独立模块，零 LLM 成本）"""

import json
from typing import Any, Dict, Optional

from loguru import logger

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
) -> None:
    """记录任务执行指标（fire-and-forget，不抛异常）"""
    if not is_kb_available():
        return

    conn_ctx = await get_pg_connection()
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
                        params, retried, retry_from_model, user_id
                    ) VALUES (
                        %(task_type)s, %(model_id)s, %(status)s, %(error_code)s,
                        %(cost_time_ms)s, %(prompt_tokens)s, %(completion_tokens)s,
                        %(prompt_category)s, %(params)s, %(retried)s,
                        %(retry_from_model)s, %(user_id)s
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
                    },
                )
            await conn.commit()
    except Exception as e:
        logger.warning(f"Knowledge metric record failed | model={model_id} | error={e}")

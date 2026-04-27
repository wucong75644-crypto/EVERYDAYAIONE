"""ERP SQL 兜底引擎 — 结构化查询失败后的最后防线。

流程：编译上下文 → 千问生成 SQL → 五重安全校验 → 只读执行。
原则：尽力而为，不增加任何新的失败模式。兜底失败 = 透明降级 = 返回 None。

设计文档：docs/document/TECH_ERP查询架构重构.md §20
"""
from __future__ import annotations

import json
import re
from typing import Any

import psycopg
import psycopg.rows
from loguru import logger

from services.kuaimai.erp_sql_schema_context import (
    ERP_SCHEMA_CONTEXT, ENUM_CONTEXT, SQL_GENERATION_PROMPT,
)


# ── 安全校验 ──────────────────────────────────────────


_DANGEROUS_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXEC)\b",
    re.IGNORECASE,
)
_ORG_ID_CHECK = re.compile(r"org_id\s*=\s*'[0-9a-f\-]+'", re.IGNORECASE)
_LIMIT_CHECK = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)


def validate_generated_sql(sql: str, org_id: str) -> tuple[bool, str]:
    """校验 LLM 生成的 SQL 是否安全。

    Returns:
        (is_valid, error_message)
    """
    # 1. 禁止写操作
    if _DANGEROUS_KEYWORDS.search(sql):
        return False, "SQL 包含危险关键字（INSERT/UPDATE/DELETE/DROP 等）"

    # 2. 必须包含 org_id 过滤（多租户隔离）
    if not _ORG_ID_CHECK.search(sql):
        return False, "SQL 缺少 org_id 过滤条件"

    # 3. org_id 值必须匹配当前用户
    if org_id not in sql:
        return False, "SQL 中的 org_id 与当前用户不匹配"

    # 4. 必须有 LIMIT
    limit_match = _LIMIT_CHECK.search(sql)
    if not limit_match:
        return False, "SQL 缺少 LIMIT 限制"

    # 5. LIMIT 不超过 1000
    limit_val = int(limit_match.group(1))
    if limit_val > 1000:
        return False, f"LIMIT {limit_val} 超过上限 1000"

    # 6. 只允许 SELECT / WITH
    stripped = sql.strip().upper()
    if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
        return False, "只允许 SELECT / WITH 查询"

    return True, ""


# ── SQL 执行（只读直连） ──────────────────────────────


async def execute_readonly_sql(
    database_url: str,
    sql: str,
    max_rows: int = 1000,
) -> tuple[list[dict], list[str]]:
    """只读执行 SQL，返回 (行列表, 列名列表)。

    安全约束：
    - 连接级只读（default_transaction_read_only = on）
    - 语句级超时（statement_timeout = 30s）
    - 行数上限 max_rows（SQL 中已有 LIMIT，这里双重保险）
    """
    async with await psycopg.AsyncConnection.connect(
        database_url,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
    ) as conn:
        async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            await cur.execute(sql)
            rows = await cur.fetchmany(max_rows)
            columns = [desc.name for desc in cur.description] if cur.description else []
    return rows, columns


# ── 触发条件判断 ──────────────────────────────────────


def should_try_sql(result: Any, query: str) -> bool:
    """判断是否启用 SQL 兜底。

    全部满足才触发：结构化查询确实失败 + 不是超时/参数错误/预警。
    """
    status = str(getattr(result, "status", ""))
    if status not in ("error", "empty"):
        return False

    summary = getattr(result, "summary", "") or ""
    error_msg = getattr(result, "error_message", "") or ""

    # 超时说明数据量大，SQL 也会超时
    if "超时" in summary or "timeout" in error_msg or status == "timeout":
        return False
    # 参数错误应该让用户修正
    if "参数" in summary or "doc_type" in error_msg:
        return False
    # 预警查询走规则引擎，SQL 兜底没意义
    metadata = getattr(result, "metadata", {}) or {}
    if metadata.get("query_type") == "alert":
        return False
    # 无法理解的请求不兜底
    if "无法理解" in summary:
        return False

    return True


# ── 动态上下文构建 ──────────────────────────────────


def build_dynamic_context(
    query: str,
    failed_summary: str,
    plan_params: dict | None,
    org_id: str,
) -> str:
    """构建本次查询的动态上下文（~300 token）。"""
    parts = [
        f"## 用户问题\n{query}",
        f"\n## 结构化查询尝试结果\n- 错误信息：{failed_summary[:200]}",
    ]

    if plan_params:
        params_str = json.dumps(plan_params, ensure_ascii=False, indent=2)
        parts.append(f"\n## 已尝试的查询参数\n```json\n{params_str}\n```")

    parts.append(
        f"\n## 安全约束\n"
        f"- 必须包含 WHERE org_id = '{org_id}'\n"
        f"- 只允许 SELECT，禁止 INSERT/UPDATE/DELETE/DROP\n"
        f"- 必须包含 LIMIT（最大 1000 行）\n"
        f"- 时间范围必须在 2 年以内"
    )

    return "\n".join(parts)


# ── 核心兜底函数 ──────────────────────────────────────


def _clean_sql(raw: str) -> str:
    """清理 LLM 输出中的 markdown 代码块标记。"""
    sql = raw.strip()
    # 去掉 ```sql ... ``` 包裹
    if sql.startswith("```"):
        first_newline = sql.find("\n")
        sql = sql[first_newline + 1:] if first_newline != -1 else sql[3:]
    if sql.endswith("```"):
        sql = sql[:-3]
    return sql.strip().rstrip(";")


async def sql_fallback(
    query: str,
    failed_result: Any,
    plan_params: dict | None,
    org_id: str,
    db: Any,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> Any | None:
    """SQL 兜底——结构化查询失败后的最后防线。

    Returns:
        AgentResult 或 None（兜底失败时透明降级）。
    """
    from core.config import get_settings
    from services.adapters.factory import create_chat_adapter
    from services.agent.agent_result import AgentResult
    from services.agent.tool_output import OutputFormat, ColumnMeta, FileRef

    logger.info(f"SQL fallback triggered | query={query[:80]}")
    settings = get_settings()

    # 1. 编译 prompt
    failed_summary = getattr(failed_result, "summary", "") or ""
    dynamic_ctx = build_dynamic_context(query, failed_summary, plan_params, org_id)
    prompt = SQL_GENERATION_PROMPT.format(
        schema_context=ERP_SCHEMA_CONTEXT,
        enum_context=ENUM_CONTEXT,
        dynamic_context=dynamic_ctx,
        org_id=org_id,
    )

    # 2. 千问生成 SQL（复用 adapter 基础设施）
    adapter = create_chat_adapter(settings.agent_loop_model, org_id=org_id, db=db)
    try:
        messages = [
            {"role": "system", "content": "你是 PostgreSQL 查询专家，只返回一条 SELECT SQL，不要任何解释。"},
            {"role": "user", "content": prompt},
        ]
        response = await adapter.chat_sync(messages=messages)
        raw_sql = getattr(response, "content", "") or ""
    except Exception as e:
        logger.warning(f"SQL generation LLM call failed: {e}")
        return None
    finally:
        await adapter.close()

    if not raw_sql.strip():
        logger.warning("SQL generation returned empty")
        return None

    sql = _clean_sql(raw_sql)

    # 3. 安全校验
    is_valid, error = validate_generated_sql(sql, org_id)
    if not is_valid:
        logger.warning(f"SQL validation failed: {error} | sql={sql[:200]}")
        return None

    # 4. 只读执行
    try:
        rows, columns = await execute_readonly_sql(settings.database_url, sql)
    except Exception as e:
        logger.warning(f"SQL execution failed: {e} | sql={sql[:200]}")
        return None

    if not rows:
        return None  # SQL 成功但无数据，不覆盖原结果

    # 5. 包装返回
    row_count = len(rows)
    summary = f"通过 SQL 查询获得 {row_count} 条结果"

    # 写 staging（如有数据行）
    file_ref = None
    if row_count > 0:
        file_ref = await _write_sql_result_to_staging(
            rows, columns, org_id, user_id, conversation_id,
        )

    logger.info(f"SQL fallback success | rows={row_count} sql={sql[:100]}")

    return AgentResult(
        status="success",
        summary=summary,
        format=OutputFormat.TABLE if row_count <= 200 else OutputFormat.FILE_REF,
        data=rows[:200] if row_count <= 200 else None,
        columns=[ColumnMeta(name=c, dtype="text", label=c) for c in columns],
        file_ref=file_ref,
        source="erp_agent",
        metadata={
            "query_type": "sql_fallback",
            "sql": sql,
            "original_error": failed_summary[:200],
        },
    )


async def _write_sql_result_to_staging(
    rows: list[dict],
    columns: list[str],
    org_id: str | None,
    user_id: str | None,
    conversation_id: str | None,
) -> Any:
    """将 SQL 结果写入 staging parquet，返回 FileRef。"""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from services.agent.tool_output import FileRef, ColumnMeta
    from services.kuaimai.erp_duckdb_helpers import resolve_export_path

    _, rel_path, staging_path, filename = resolve_export_path(
        "sql_fallback", user_id, org_id, conversation_id,
    )

    # 所有列用 string 类型
    schema = pa.schema([(c, pa.string()) for c in columns])
    str_rows = [
        {c: (str(row[c]) if row.get(c) is not None else None) for c in columns}
        for row in rows
    ]
    table = pa.Table.from_pylist(str_rows, schema=schema)
    pq.write_table(table, str(staging_path), compression="snappy")

    size_bytes = staging_path.stat().st_size
    return FileRef(
        path=str(staging_path),
        filename=filename,
        format="parquet",
        row_count=len(rows),
        size_bytes=size_bytes,
        columns=[ColumnMeta(name=c, dtype="text", label=c) for c in columns],
        created_by="sql_fallback",
    )

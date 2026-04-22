"""
部门Agent基类。

每个部门Agent只管自己的业务域，理解本域语义，校验本域参数。
不跨域、不做计算。

设计文档: docs/document/TECH_多Agent单一职责重构.md §6.1 / §9.6 / §13.3-13.5
"""
from __future__ import annotations

import asyncio
import time as _time
import uuid as _uuid
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from services.agent.department_types import ValidationResult
from services.agent.tool_output import (
    ColumnMeta,
    FileRef,
    OutputFormat,
    OutputStatus,
    ToolOutput,
    _FORMAT_MIME,
)


# 全局标准字段名（跨Agent统一语义）
CANONICAL_FIELDS = {
    "product_code", "sku_code", "platform", "shop_name",
    "warehouse_name", "doc_type", "order_no",
}

# 数据分流阈值：≤200行内联，>200行写文件
# v6: INLINE_THRESHOLD 已废弃，统一走 staging + 摘要


class DepartmentAgent(ABC):
    """部门Agent基类。

    子类必须实现：domain / tools / system_prompt / validate_params
    子类可覆盖：FIELD_MAP / allowed_doc_types
    """

    # ── 子类覆盖 ──
    FIELD_MAP: dict[str, str] = {}
    """底层字段名 → 标准字段名映射。
    例：{"outer_id": "product_code", "sku_outer_id": "sku_code"}
    由基类 _build_output 统一处理（同步映射 data key + ColumnMeta.name）。
    """

    allowed_doc_types: list[str] = []
    """允许查询的 doc_type 白名单。_query_local_data 强制校验。"""

    def __init__(
        self,
        db: Any,
        org_id: str | None = None,
        request_ctx: Any = None,
        staging_dir: str | None = None,
        budget: Any = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
    ):
        self.db = db
        self.org_id = org_id
        self.request_ctx = request_ctx
        self._staging_dir = staging_dir
        self._budget = budget  # v6: ExecutionBudget（可选）
        self._user_id = user_id
        self._conversation_id = conversation_id

    # ── 抽象属性 ──

    @property
    @abstractmethod
    def domain(self) -> str:
        """业务域标识：warehouse / purchase / trade / aftersale"""

    @property
    @abstractmethod
    def tools(self) -> list[str]:
        """本域可用工具列表"""

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """本域专用 system prompt"""

    @abstractmethod
    def validate_params(self, action: str, params: dict) -> ValidationResult:
        """本域参数校验。

        返回 ValidationResult：
        - ok: 参数齐全，可执行
        - missing: 缺少必填参数，返回协商提示
        - conflict: 参数互斥，返回冲突说明
        """

    # ── 通用校验（基类提供，子类不需要重复实现）──

    def _validate_time_range(self, time_range_str: str) -> ValidationResult | None:
        """校验已标准化的时间范围字符串。

        支持格式：
        - YYYY-MM-DD ~ YYYY-MM-DD（天级）
        - YYYY-MM-DD HH:MM ~ YYYY-MM-DD HH:MM（分钟级）

        ERPAgent 的 LLM 层已经把"上个月"转成了标准化参数，
        部门Agent收到的是已标准化的字符串。
        返回 None 表示校验通过。
        """
        try:
            start_str, end_str = time_range_str.split(" ~ ")
            start_str, end_str = start_str.strip(), end_str.strip()
            # 兼容纯日期和带时分的格式
            start = datetime.fromisoformat(start_str) if " " in start_str else datetime.fromisoformat(start_str + " 00:00")
            end = datetime.fromisoformat(end_str) if " " in end_str else datetime.fromisoformat(end_str + " 23:59")
        except (ValueError, AttributeError):
            return ValidationResult.conflict(
                f"时间范围格式错误: {time_range_str}，应为 YYYY-MM-DD ~ YYYY-MM-DD 或 YYYY-MM-DD HH:MM ~ YYYY-MM-DD HH:MM",
            )
        if end <= start:
            return ValidationResult.conflict("结束时间必须晚于开始时间（不能相同）")
        if (end - start).days > 90:
            return ValidationResult.conflict("时间范围不能超过90天")
        return None

    def _validate_required(
        self, params: dict, required: list[str],
    ) -> ValidationResult | None:
        """校验必填参数。返回 None 表示全部存在。"""
        missing = [k for k in required if not params.get(k)]
        return ValidationResult.missing(missing) if missing else None

    # ── 状态判定（基类统一逻辑，子类不自行发挥）──

    def _determine_status(
        self,
        rows: list,
        error: Exception | None = None,
        is_truncated: bool = False,
        total_expected: int | None = None,
    ) -> tuple[OutputStatus, dict]:
        """判定执行状态。

        返回 (status, extra_metadata)。
        extra_metadata 只在有意义时才有值（如 total_expected）。
        """
        if error:
            return OutputStatus.ERROR, {}
        if is_truncated:
            meta: dict[str, Any] = {}
            if total_expected is not None:
                meta["total_expected"] = total_expected
            return OutputStatus.PARTIAL, meta
        if not rows:
            return OutputStatus.EMPTY, {}
        return OutputStatus.OK, {}

    # ── 构建 ToolOutput（核心方法）──

    def _build_output(
        self,
        rows: list[dict],
        summary: str,
        columns: list[ColumnMeta],
        *,
        status: OutputStatus = OutputStatus.OK,
        error_message: str = "",
        staging_dir: str | None = None,
        **business_fields: Any,
    ) -> ToolOutput:
        """构建 ToolOutput，自动处理 FIELD_MAP 和数据分流。

        v6: 统一走 staging + 摘要（~238 token），取消 inline 模式。
        无 staging_dir 或空数据时降级为 TEXT 摘要。
        FIELD_MAP 自动映射 data key 和 ColumnMeta.name。
        FILE_REF 会被下游同步读取，rows 应为 top-N 结果，避免全量数据。
        """
        # ── FIELD_MAP 标准化（data 和 columns 同步映射）──
        if self.FIELD_MAP:
            rows = [
                {self.FIELD_MAP.get(k, k): v for k, v in row.items()}
                for row in rows
            ]
            columns = [
                ColumnMeta(
                    name=self.FIELD_MAP.get(col.name, col.name),
                    dtype=col.dtype,
                    label=col.label,
                )
                for col in columns
            ]

        base = dict(
            summary=summary,
            source=self.domain,
            status=status,
            error_message=error_message,
            columns=columns,
            metadata=business_fields,
        )

        # v6: 统一走 staging + 摘要（~238 token），取消 inline
        if not staging_dir:
            # 无 staging_dir（测试/降级场景）→ TEXT 摘要
            return ToolOutput(format=OutputFormat.TEXT, **base)

        file_ref, profile_text, profile_stats = self._write_to_staging(
            rows, columns, staging_dir,
        )
        base["summary"] = profile_text
        if profile_stats:
            base["metadata"] = {**base.get("metadata", {}), "stats": profile_stats}
        return ToolOutput(format=OutputFormat.FILE_REF, file_ref=file_ref, **base)

    def _write_to_staging(
        self,
        rows: list[dict],
        columns: list[ColumnMeta],
        staging_dir: str,
    ) -> tuple[FileRef, str, dict]:
        """将数据写入 staging parquet 并生成 profile 摘要。

        Returns:
            (file_ref, profile_text, stats_dict)
        """
        import json as _json

        start = _time.time()
        ts = int(start)
        filename = f"{self.domain}_{ts}.parquet"
        staging_path = Path(staging_dir)
        staging_path.mkdir(parents=True, exist_ok=True)
        file_path = staging_path / filename

        try:
            import pandas as pd
            df = pd.DataFrame(rows)
            df.to_parquet(file_path, index=False)
            size_bytes = file_path.stat().st_size
        except Exception as e:
            logger.error(f"Write staging parquet failed: {e}")
            # 降级为 JSON
            filename = f"{self.domain}_{ts}.json"
            file_path = staging_path / filename
            file_path.write_text(
                _json.dumps(rows, ensure_ascii=False, default=str),
            )
            size_bytes = file_path.stat().st_size
            df = pd.DataFrame(rows)

        elapsed = _time.time() - start

        # 生成标准数据摘要（v6: 返回 text + stats_dict）
        from services.agent.data_profile import build_data_profile
        profile_text, _profile_stats = build_data_profile(
            df=df,
            filename=filename,
            file_size_kb=size_bytes / 1024,
            elapsed=elapsed,
        )

        fmt = "parquet" if filename.endswith(".parquet") else "json"
        file_ref = FileRef(
            path=str(file_path),
            filename=filename,
            format=fmt,
            row_count=len(rows),
            size_bytes=size_bytes,
            columns=columns,
            preview=profile_text,
            created_at=_time.time(),
            id=_uuid.uuid4().hex,
            mime_type=_FORMAT_MIME.get(fmt, ""),
            created_by=self.domain,
        )
        return file_ref, profile_text, _profile_stats

    # ── Context 注入（确定性提取，不靠 LLM）──

    # FILE_REF 读取 sentinel：同步方法，用 sentinel 防并发双读。
    # 双读结果正确（同一文件同一列），接受此权衡。
    # Boundary: parquet 须为 top-N 结果（INLINE_THRESHOLD=200），
    # 全量数据需改为 asyncio.to_thread。
    _COL_CACHE_LOADING = object()

    def _extract_field_from_context(
        self,
        context: list[ToolOutput] | None,
        field_name: str,
    ) -> list[Any]:
        """从上游 context 提取指定字段值。支持 inline + FILE_REF。
        零值保护（if val is not None）。FILE_REF 结果缓存在 metadata 上。
        """
        values: list[Any] = []
        for output in (context or []):
            # 判断列是否包含目标字段
            all_cols = output.columns or (
                output.file_ref.columns if output.file_ref else []
            )
            has_field = any(c.name == field_name for c in all_cols)
            if not has_field:
                continue

            # 路径1: 内联数据
            if output.data:
                for row in output.data:
                    val = row.get(field_name)
                    if val is not None:
                        values.append(val)
                continue

            # 路径2: FILE_REF — 只读目标列，结果缓存在 output.metadata 上
            if output.file_ref and output.file_ref.path:
                cache_key = f"_col_cache:{field_name}"
                cached = output.metadata.get(cache_key)
                if cached is self._COL_CACHE_LOADING:
                    # 另一个协程正在读，直接读文件（ms级IO，接受双读）
                    try:
                        import pandas as pd
                        df = pd.read_parquet(
                            output.file_ref.path, columns=[field_name],
                        )
                        values.extend(df[field_name].dropna().tolist())
                    except Exception as e:
                        logger.warning(
                            f"Extract {field_name} from FILE_REF failed: {e}",
                        )
                    continue
                if cached is not None:
                    values.extend(cached)
                    continue
                # 占位 → 读取 → 写入
                output.metadata[cache_key] = self._COL_CACHE_LOADING
                try:
                    import pandas as pd
                    df = pd.read_parquet(
                        output.file_ref.path, columns=[field_name],
                    )
                    vals = df[field_name].dropna().tolist()
                    output.metadata[cache_key] = vals
                    values.extend(vals)
                except Exception as e:
                    output.metadata.pop(cache_key, None)
                    logger.warning(
                        f"Extract {field_name} from FILE_REF failed: {e}",
                    )
        return values

    # ── 语义参数 → filters DSL 转换（委托 param_converter）──

    @staticmethod
    def _params_to_filters(params: dict) -> list[dict]:
        """把 PlanBuilder 输出的语义参数转成 UnifiedQueryEngine 的 filters DSL。"""
        from services.agent.param_converter import params_to_filters
        return params_to_filters(params)

    # ── L3 诊断（委托 param_converter）──

    @staticmethod
    def _diagnose_empty(filters: list[dict]) -> str:
        """L3：查询返回空结果时，根据 filters 生成诊断建议。"""
        from services.agent.param_converter import diagnose_empty
        return diagnose_empty(filters)

    @staticmethod
    def _diagnose_error(error_msg: str) -> str:
        """L3：查询失败时，根据错误信息给出重试建议。"""
        from services.agent.param_converter import diagnose_error
        return diagnose_error(error_msg)

    # ── doc_type 白名单强制校验 ──

    async def _query_local_data(
        self, doc_type: str, **kwargs: Any,
    ) -> ToolOutput:
        """封装 local_data 调用，强制 doc_type 白名单校验。

        显式提取已知参数，未知参数通过 execute(**_kwargs) 吸收丢弃，
        防止 LLM 注入任意参数到查询引擎。
        """
        if doc_type not in self.allowed_doc_types:
            return ToolOutput(
                summary=(
                    f"{self.domain} Agent 无权查询 {doc_type} 类型数据"
                ),
                format=OutputFormat.TEXT,
                source=self.domain,
                status=OutputStatus.ERROR,
                error_message=(
                    f"doc_type={doc_type} 不在 {self.domain} 的白名单 "
                    f"{self.allowed_doc_types} 中"
                ),
            )
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        engine = UnifiedQueryEngine(db=self.db, org_id=self.org_id)
        mode = kwargs.get("mode", "summary")
        filters = kwargs.get("filters", [])
        result = await engine.execute(
            doc_type=doc_type,
            mode=mode,
            filters=filters,
            group_by=kwargs.get("group_by"),
            sort_by=kwargs.get("sort_by"),
            sort_dir=kwargs.get("sort_dir", "desc"),
            fields=kwargs.get("fields"),
            limit=kwargs.get("limit", 20),
            time_type=kwargs.get("time_type"),
            include_invalid=kwargs.get("include_invalid", False),
            request_ctx=self.request_ctx,
            user_id=self._user_id,
            conversation_id=self._conversation_id,
        )
        # L3：空结果诊断
        if result.status == "empty" and filters:
            diagnosis = self._diagnose_empty(filters)
            if diagnosis:
                result.summary += f"\n\n诊断建议：\n{diagnosis}"
                logger.info(f"L3 空结果诊断: doc_type={doc_type}, {diagnosis}")
        # L3：失败诊断——根据错误类型给出重试建议
        if result.status == "error":
            hint = self._diagnose_error(result.error_message)
            if hint:
                result.summary += f"\n\n重试建议：{hint}"
                logger.info(f"L3 失败诊断: doc_type={doc_type}, {hint}")

        return result

    # ── 从 params 提取通用查询 kwargs（供 _dispatch 透传给 _query_local_data）──

    @staticmethod
    def _query_kwargs(params: dict) -> dict[str, Any]:
        """从 merged params 提取 _query_local_data 接受的通用参数。

        解决所有子 Agent _dispatch 只挑选部分参数导致 fields/sort/limit 丢失的问题。
        """
        kw: dict[str, Any] = {
            "mode": params.get("mode", "summary"),
            "filters": params.get("filters", []),
        }
        # 可���参数：只在存在时传，避免覆盖引擎默认值
        for key in (
            "group_by", "include_invalid", "fields",
            "sort_by", "sort_dir", "limit",
        ):
            val = params.get(key)
            if val is not None:
                kw[key] = val
        return kw

    # ── 写操作检测 ──

    _WRITE_ACTIONS = frozenset({
        "create", "update", "delete", "modify",
        "adjust", "cancel", "batch_update",
    })
    _WRITE_KEYWORDS = frozenset({
        "修改", "删除", "创建", "调整", "取消", "新建", "更新", "批量",
    })

    def _is_write_action(self, action: str) -> bool:
        """判断 action 是否为写操作。子类可覆盖添加域特定写操作。"""
        return action in self._WRITE_ACTIONS

    def _has_write_intent(self, task: str) -> bool:
        """从任务描述关键词检测写意图（兜底保护）。"""
        return any(kw in task for kw in self._WRITE_KEYWORDS)

    # ── DAG 执行入口（Phase 2B）──

    async def execute(
        self,
        task: str,
        context: list[ToolOutput] | None = None,
        *,
        dag_mode: bool = False,
        params: dict | None = None,
    ) -> ToolOutput:
        """统一执行入口（DAG 编排器调用）。

        params: Round.params（PlanBuilder LLM 输出的静态语义参数）。
                动态参数（product_code）从 context 提取，合并到 params。
        dag_mode=True 时禁止写操作。
        """
        action = self._classify_action(task)

        # DAG 模式下禁止写操作（双重检查：action 枚举 + 任务描述关键词）
        if dag_mode and (
            self._is_write_action(action) or self._has_write_intent(task)
        ):
            return ToolOutput(
                summary=(
                    "DAG 模式下暂不支持写操作，请单独执行该操作"
                ),
                source=self.domain,
                status=OutputStatus.ERROR,
                error_message=(
                    f"write blocked in dag_mode | action={action}"
                ),
            )

        # 合并参数：静态（PlanBuilder）+ 动态（context）
        merged = dict(params or {})
        # 从 context 提取动态参数（跨域传递的 product_code）
        if context:
            codes = self._extract_field_from_context(
                context, "product_code",
            )
            if codes:
                merged.setdefault("product_codes", codes)
                # 兼容：部门 Agent 的 validate_params 和 _dispatch 可能用单数
                merged.setdefault(
                    "product_code",
                    codes[0] if len(codes) == 1 else codes,
                )
        # 语义参数 → filters DSL（确定性转换）
        # 只要没有预设 filters 就调用转换（不再要求必须有 time_range）
        if "filters" not in merged:
            merged["filters"] = self._params_to_filters(merged)

        validation = self.validate_params(action, merged)
        if not validation.is_ok:
            return ToolOutput(
                summary=validation.message,
                source=self.domain,
                status=OutputStatus.ERROR,
                error_message=validation.message,
            )

        # v6: partial rows 暂存（超时 cancel 时可返回已获取的部分数据）
        self._partial_rows: list[dict] = []
        try:
            result = await self._dispatch(action, merged, context)
            # 降级标记（v6: 纯结构化，不拼文本前缀）
            if merged.get("_degraded") and result.status != "error":
                result = ToolOutput(
                    summary=result.summary,
                    format=result.format,
                    source=result.source,
                    status=result.status,
                    columns=result.columns,
                    data=result.data,
                    file_ref=result.file_ref,
                    metadata={**result.metadata, "_degraded": True},
                )
            return result
        except asyncio.CancelledError:
            # v6: 超时 cancel 时返回已获取的部分数据
            if self._partial_rows:
                logger.warning(
                    f"{self.domain} Agent cancelled with {len(self._partial_rows)} partial rows",
                )
                return ToolOutput(
                    summary=f"{self.domain} 查询超时，返回已获取的 {len(self._partial_rows)} 条部分数据",
                    format=OutputFormat.TABLE,
                    source=self.domain,
                    status=OutputStatus.PARTIAL,
                    data=self._partial_rows,
                )
            raise  # 无 partial 数据则继续传播
        except Exception as e:
            logger.error(
                f"{self.domain} Agent execute failed | "
                f"action={action} | error={e}",
            )
            return ToolOutput(
                summary=f"{self.domain} 查询失败: {e}",
                source=self.domain,
                status=OutputStatus.ERROR,
                error_message=str(e),
            )

    def _classify_action(self, task: str) -> str:
        """从任务描述关键词分类 action。子类应覆盖。"""
        return "default"

    # 参数提取已内联到 execute()：静态从 Round.params，动态从 context

    async def _dispatch(
        self,
        action: str,
        params: dict[str, Any],
        context: list[ToolOutput] | None,
    ) -> ToolOutput:
        """分发到具体查询方法。子类必须覆盖。"""
        return ToolOutput(
            summary=f"{self.domain} Agent 未实现 action={action}",
            source=self.domain,
            status=OutputStatus.ERROR,
            error_message=f"unimplemented action: {action}",
        )

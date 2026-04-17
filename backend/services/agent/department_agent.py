"""
部门Agent基类。

每个部门Agent只管自己的业务域，理解本域语义，校验本域参数。
不跨域、不做计算。

设计文档: docs/document/TECH_多Agent单一职责重构.md §6.1 / §9.6 / §13.3-13.5
"""
from __future__ import annotations

import time as _time
from abc import ABC, abstractmethod
from datetime import date
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
)


# 全局标准字段名（跨Agent统一语义）
CANONICAL_FIELDS = {
    "product_code", "sku_code", "platform", "shop_name",
    "warehouse_name", "doc_type", "order_no",
}

# 数据分流阈值：≤200行内联，>200行写文件
INLINE_THRESHOLD = 200


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

    def __init__(self, db: Any, org_id: str | None = None):
        self.db = db
        self.org_id = org_id

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
        """校验已标准化的时间范围字符串（格式：YYYY-MM-DD ~ YYYY-MM-DD）。

        ERPAgent 的 LLM 层已经把"上个月"转成了标准化参数，
        部门Agent收到的是已标准化的字符串。
        返回 None 表示校验通过。
        """
        try:
            start_str, end_str = time_range_str.split(" ~ ")
            start = date.fromisoformat(start_str.strip())
            end = date.fromisoformat(end_str.strip())
        except (ValueError, AttributeError):
            return ValidationResult.conflict(
                f"时间范围格式错误: {time_range_str}，应为 YYYY-MM-DD ~ YYYY-MM-DD",
            )
        if end < start:
            return ValidationResult.conflict("结束日期不能早于开始日期")
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

        - source 从 self.domain 自动取（协议层必填）
        - columns 必须传（协议层必填）
        - business_fields 全部放 metadata（业务层动态）
        - FIELD_MAP 自动映射 data key 和 ColumnMeta.name（同步）

        ≤200行 → TABLE（inline JSON）
        >200行 → FILE_REF（写 staging parquet）

        注意：FILE_REF 路径的 parquet 文件会被下游 _extract_field_from_context
        同步读取。调用方应保证传入的 rows 是筛选后的结果（top-N），
        不是全量数据。全量数据写大文件会阻塞 asyncio 事件循环。
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

        if len(rows) <= INLINE_THRESHOLD:
            return ToolOutput(format=OutputFormat.TABLE, data=rows, **base)

        # >200行走文件
        if not staging_dir:
            logger.warning(
                f"{self.domain} Agent: >200行但无 staging_dir，降级为内联",
            )
            return ToolOutput(format=OutputFormat.TABLE, data=rows, **base)

        file_ref = self._write_to_staging(rows, columns, staging_dir)
        return ToolOutput(format=OutputFormat.FILE_REF, file_ref=file_ref, **base)

    def _write_to_staging(
        self,
        rows: list[dict],
        columns: list[ColumnMeta],
        staging_dir: str,
    ) -> FileRef:
        """将数据写入 staging parquet 文件。"""
        import json as _json

        ts = int(_time.time())
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

        # 前3行预览
        preview_rows = rows[:3]
        preview = "\n".join(
            _json.dumps(r, ensure_ascii=False, default=str)
            for r in preview_rows
        )

        return FileRef(
            path=str(file_path),
            filename=filename,
            format="parquet" if filename.endswith(".parquet") else "json",
            row_count=len(rows),
            size_bytes=size_bytes,
            columns=columns,
            preview=preview,
            created_at=_time.time(),
        )

    # ── Context 注入（确定性提取，不靠 LLM）──

    # FILE_REF 读取占位符：标记"正在读取"，防止并发协程重复读文件
    # 注：使用 sentinel 而非 asyncio.Event，原因：
    # _extract_field_from_context 是同步方法，被同步调用链使用
    # （_extract_params_from_task → validate_params）。
    # 改为 async 需要追溯整条调用链。
    # 双读结果正确，parquet 文件通常较小（top-N 结果，毫秒级 IO），
    # 接受此权衡。
    #
    # Boundary conditions（满足以下两个条件时阻塞可忽略）：
    # 1. INLINE_THRESHOLD=200 保证 parquet 只有 >200 行才写文件
    # 2. 跨域传递的是筛选后的 top-N 结果，不是全量数据（调用方保证）
    # 如果未来任一条件被破坏（阈值调大 / 上游写全量数据），
    # 需要改为 asyncio.to_thread(pd.read_parquet, ...) 避免阻塞事件循环。
    _COL_CACHE_LOADING = object()

    def _extract_field_from_context(
        self,
        context: list[ToolOutput] | None,
        field_name: str,
    ) -> list[Any]:
        """从上游 context 里提取指定字段的值列表。

        支持 inline data 和 FILE_REF 两种模式。
        确定性提取，不靠 LLM。
        零值保护：if val is not None（不用 if val），
        库存为0的商品是缺货分析的核心数据，不能被静默丢弃。
        FILE_REF 读取结果缓存在 ToolOutput.metadata 上，
        同一个文件在同一次 DAG 执行里只读一次。
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

    # ── doc_type 白名单强制校验 ──

    async def _query_local_data(
        self, doc_type: str, **kwargs: Any,
    ) -> ToolOutput:
        """封装 local_data 调用，强制 doc_type 白名单校验。"""
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
        return await engine.execute(
            doc_type=doc_type,
            mode=kwargs.pop("mode", "summary"),
            filters=kwargs.pop("filters", []),
            **kwargs,
        )

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
    ) -> ToolOutput:
        """统一执行入口（DAG 编排器调用）。

        dag_mode=True 时禁止写操作（DAG 路径下不支持 ask_user 打断恢复）。

        1. 从 task 关键词分类 action
        2. 从 task + context 提取参数
        3. 校验参数
        4. 分发到具体查询方法

        子类覆盖 _classify_action / _dispatch 即可。
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

        params = self._extract_params_from_task(task, context)

        validation = self.validate_params(action, params)
        if not validation.is_ok:
            return ToolOutput(
                summary=validation.message,
                source=self.domain,
                status=OutputStatus.ERROR,
                error_message=validation.message,
            )

        try:
            return await self._dispatch(action, params, context)
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

    def _extract_params_from_task(
        self,
        task: str,
        context: list[ToolOutput] | None,
    ) -> dict[str, Any]:
        """从任务描述 + context 提取参数。

        基类提供 context 字段提取，子类可覆盖添加更多逻辑。
        """
        params: dict[str, Any] = {}
        # 从 context 提取 product_code（跨域传递的核心字段）
        if context:
            codes = self._extract_field_from_context(context, "product_code")
            if codes:
                params["product_code"] = codes[0] if len(codes) == 1 else codes
        return params

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

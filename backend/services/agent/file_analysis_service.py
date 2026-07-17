"""file_analyze 的路径解析、格式转换与结果登记。"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from services.agent.agent_result import AgentResult


async def analyze_file(
    owner: Any,
    executor: Any,
    args: dict[str, Any],
    settings: Any,
) -> AgentResult:
    """编排文件分析；各阶段返回结构化结果，不跨阶段吞异常。"""
    from core.workspace import resolve_staging_dir
    from services.agent.file_path_cache import get_file_cache

    cache = get_file_cache(owner.conversation_id)
    resolved = _resolve_analysis_path(owner, executor, args, cache)
    if isinstance(resolved, AgentResult):
        return resolved
    abs_path, display_path = resolved
    scope_error = _validate_resource_scope(
        owner, executor, args, abs_path, display_path,
    )
    if scope_error:
        return scope_error
    validation = _validate_analysis_file(
        abs_path, display_path, owner._ANALYZE_EXTENSIONS,
    )
    if validation:
        return validation
    staging_dir = resolve_staging_dir(
        settings.file_workspace_root,
        owner.workspace_user_id,
        getattr(owner, "org_id", None),
        owner.conversation_id,
    )
    if not cache._staging_dir:
        cache.set_staging_dir(staging_dir)
    started_at = time.monotonic()
    converted = await _convert_to_parquet(
        executor, cache, abs_path, staging_dir,
    )
    if isinstance(converted, AgentResult):
        return converted
    cache_path, sheet_names = converted
    return _build_analysis_result(
        executor,
        cache,
        abs_path,
        cache_path,
        staging_dir,
        sheet_names,
        round(time.monotonic() - started_at, 2),
    )


def _validate_resource_scope(
    owner: Any,
    executor: Any,
    args: dict[str, Any],
    abs_path: str,
    display_path: str,
) -> AgentResult | None:
    manifest = getattr(owner, "resource_manifest", None)
    if manifest is None or args.get("scope") == "workspace":
        return None
    try:
        relative = str(
            Path(abs_path).relative_to(Path(executor.workspace_root))
        )
    except ValueError:
        relative = ""
    if relative in manifest.allowed_paths:
        return None
    return _error(
        f"文件不属于当前任务资源: {display_path}",
        "RESOURCE_PATH_NOT_IN_MANIFEST",
        False,
    )


def _resolve_analysis_path(
    owner: Any,
    executor: Any,
    args: dict[str, Any],
    cache: Any,
) -> tuple[str, str] | AgentResult:
    from services.agent.file_id import is_valid_fid, resolve_fid_to_workspace

    file_id = (args.get("file_id") or "").strip()
    path = (args.get("path") or "").strip()
    abs_path: str | None = None
    if file_id:
        if not is_valid_fid(file_id):
            return _error(
                f"file_id 格式错误: {file_id}",
                f"file_id 必须是 fid_xxx 格式（fid_ + 8 位十六进制）。"
                f"你传的是 {file_id!r}。请从 <attachments> 的 <id> 字段 copy。",
                True,
            )
        abs_path = resolve_fid_to_workspace(
            file_id, getattr(owner, "org_id", None), cache,
        )
        if not abs_path:
            return _error(
                f"未找到 file_id={file_id}",
                f"file_id={file_id} 在当前对话的附件里找不到。"
                "请检查 <attachments> 块的 <id> 字段。",
                True,
            )
        path = file_id
    if not abs_path and not path:
        return _error(
            "请提供 file_id 或 path",
            "file_id 或 path 至少传一个",
            True,
        )
    if not abs_path:
        abs_path = cache.resolve(path, usage="analyze")
    if abs_path:
        return abs_path, path
    return _resolve_legacy_path(owner, executor, path)


def _resolve_legacy_path(
    owner: Any,
    executor: Any,
    path: str,
) -> tuple[str, str] | AgentResult:
    try:
        return str(executor.resolve_safe_path(path)), path
    except (FileNotFoundError, IsADirectoryError) as error:
        return _error(f"文件不存在: {path}", str(error), True)
    except (PermissionError, OSError, ValueError) as error:
        logger.warning(
            f"file_analyze path rejected | conv={owner.conversation_id} "
            f"| path={path!r} | reason={type(error).__name__}: {error}"
        )
        return _error(f"路径不允许: {path}", str(error), False)
    except Exception as error:
        return _error(f"路径解析失败: {path}", str(error), True)


def _validate_analysis_file(
    abs_path: str,
    display_path: str,
    allowed_extensions: set[str],
) -> AgentResult | None:
    if not os.path.isfile(abs_path):
        return _error(
            f"文件不存在: {display_path}",
            f"Not a file: {abs_path}",
            True,
        )
    extension = Path(abs_path).suffix.lower()
    if extension not in allowed_extensions:
        return _error(
            f"file_analyze 仅支持 Excel/CSV 文件，当前文件类型: {extension}",
            f"Unsupported extension: {extension}",
            False,
        )
    return None


async def _convert_to_parquet(
    executor: Any,
    cache: Any,
    abs_path: str,
    staging_dir: str,
) -> tuple[str, list[str] | None] | AgentResult:
    from services.agent.data_query_cache import (
        _ENSURE_CACHE_TIMEOUT,
        ensure_parquet_cache,
        ensure_parquet_cache_csv,
        validate_xlsx_safety,
    )
    from services.agent.file_ai_judge import FileAnalyzeError

    extension = Path(abs_path).suffix.lower()
    try:
        if extension in {".xlsx", ".xls"}:
            validate_xlsx_safety(abs_path)
        converter = (
            ensure_parquet_cache_csv
            if extension in {".csv", ".tsv"}
            else None
        )
        operation = (
            converter(abs_path, staging_dir)
            if converter
            else ensure_parquet_cache(abs_path, None, staging_dir)
        )
        return await asyncio.wait_for(
            operation, timeout=_ENSURE_CACHE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        name = Path(abs_path).name
        cache.register(name, workspace=abs_path)
        result = _error(
            f"文件「{name}」分析超时（> {_ENSURE_CACHE_TIMEOUT}s）",
            f"ensure_parquet_cache timeout ({_ENSURE_CACHE_TIMEOUT}s)",
            True,
        )
        result.metadata.update({
            "error_category": "timeout",
            "suggested_action": "retry_immediately",
        })
        return result
    except FileAnalyzeError as error:
        _register_source(executor, cache, abs_path)
        return AgentResult(
            summary=error.user_message or error.error_summary,
            status="error",
            error_message=error.error_summary,
            metadata=error.to_metadata(),
        )
    except ValueError as error:
        return _error(str(error), str(error), False)
    except Exception as error:
        return _error(f"文件解析失败: {error}", str(error), False)


def _build_analysis_result(
    executor: Any,
    cache: Any,
    abs_path: str,
    cache_path: str,
    staging_dir: str,
    sheet_names: list[str] | None,
    elapsed: float,
) -> AgentResult:
    from services.agent.file_meta import read_file_meta
    from services.agent.file_xml_renderer import render_xml

    try:
        parquet_path = _sandbox_parquet_path(cache_path, staging_dir)
    except (FileNotFoundError, ValueError) as error:
        logger.error(
            f"file_analyze parquet contract invalid | "
            f"file={Path(abs_path).name} | reason={error}"
        )
        return _error(
            "文件转换完成，但 Parquet 访问路径无效，请重新分析。",
            f"PARQUET_PATH_CONTRACT_INVALID:{type(error).__name__}",
            True,
        )
    meta = read_file_meta(cache_path)
    if meta is None:
        logger.error(
            f"file_analyze metadata missing | file={Path(abs_path).name}"
        )
        return _error(
            "文件转换完成，但分析元数据缺失，请重新分析。",
            "PARQUET_METADATA_MISSING",
            True,
        )
    file_view = render_xml(
        meta,
        parquet_path=parquet_path,
        original_path=_sandbox_original_path(abs_path, executor.workspace_root),
        related_files=meta.related_files,
    )
    name = Path(abs_path).name
    _register_source(executor, cache, abs_path)
    cache.set_parquet(name, cache_path)
    cache.set_analyzed(name, True)
    lines = [file_view]
    if sheet_names and len(sheet_names) > 1:
        lines.extend(["", f"Sheet 列表: {', '.join(sheet_names)}"])
    _log_analysis_success(name, meta, elapsed)
    return AgentResult(summary="\n".join(lines), status="success")


def _sandbox_parquet_path(cache_path: str, staging_dir: str) -> str:
    """把真实缓存路径投影为 code_execute 可直接读取的 staging 相对路径。"""
    resolved_cache = Path(cache_path).resolve()
    if not resolved_cache.is_file():
        raise FileNotFoundError("converted parquet does not exist")
    resolved_staging = Path(staging_dir).resolve()
    try:
        relative = resolved_cache.relative_to(resolved_staging)
    except ValueError as error:
        raise ValueError("converted parquet is outside staging") from error
    return (Path("staging") / relative).as_posix()


def _sandbox_original_path(abs_path: str, workspace_root: str) -> str:
    """返回沙盒视角的原文件路径；外部资源仅暴露文件名。"""
    try:
        return Path(abs_path).resolve().relative_to(
            Path(workspace_root).resolve()
        ).as_posix()
    except ValueError:
        return Path(abs_path).name


def _register_source(
    executor: Any,
    cache: Any,
    abs_path: str,
) -> None:
    name = Path(abs_path).name
    cache.register(name, workspace=abs_path)
    try:
        relative = str(
            Path(abs_path).relative_to(Path(executor.workspace_root))
        )
        cache.register(relative, workspace=abs_path)
    except ValueError:
        pass


def _log_analysis_success(name: str, meta: Any, elapsed: float) -> None:
    ai = (meta.ai_decision if meta else None) or {}
    path_type = (
        meta.schema.get("path_type")
        if meta and meta.schema else None
    ) or "?"
    logger.info(
        f"file_analyze OK | {name} | "
        f"{meta.summary.get('row_count', '?') if meta else '?'}×"
        f"{meta.summary.get('col_count', '?') if meta else '?'} | "
        f"path={path_type} | model={ai.get('model_used', '?')} | "
        f"ai_attempts={ai.get('attempt_count', '?')} | "
        f"ai_ms={ai.get('elapsed_ms', '?')} | total={elapsed}s"
    )


def _error(
    summary: str,
    error_message: str,
    retryable: bool,
) -> AgentResult:
    return AgentResult(
        summary=summary,
        status="error",
        error_message=error_message,
        metadata={"retryable": retryable},
    )

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
    from services.file_resources import FileTargetResolver
    from services.tools.file_calls import active_file_call, validate_selectors
    prepared = active_file_call("file_analyze")
    try:
        validate_selectors(args)
        if prepared is not None:
            target = prepared.targets[0]
        else:
            resolver = FileTargetResolver(owner, executor, scope=args.get("scope") or "current")
            selectors = [args.get("resource_ref"), args.get("file_id"), args.get("path")]
            targets = [resolver.resolve(value) for value in selectors if value]
            if not targets:
                return _error("请提供 resource_ref、file_id 或 path", "RESOURCE_TARGET_REQUIRED", True)
            if len({item.path for item in targets}) != 1:
                return _error("文件参数指向不同目标", "RESOURCE_SELECTOR_CONFLICT", False)
            target = targets[0]
        target.validate()
        return str(target.path), args.get("file_id") or args.get("path") or str(target.path.relative_to(Path(executor.workspace_root)))
    except (ValueError, OSError) as error:
        retryable = getattr(error, "code", "") in {
            "RESOURCE_NOT_FOUND", "RESOURCE_TARGET_REQUIRED", "RESOURCE_REFERENCE_INVALID", "RESOURCE_AMBIGUOUS",
        }
        summary = f"路径不允许: {error}" if isinstance(error, PermissionError) else str(error)
        return _error(summary, str(error), retryable)


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
        from services.file_resources import source_snapshot
        async with source_snapshot(abs_path, staging_dir) as snapshot:
            if extension in {".xlsx", ".xls"}:
                validate_xlsx_safety(snapshot)
            operation = (
                ensure_parquet_cache_csv(snapshot, staging_dir)
                if extension in {".csv", ".tsv"}
                else ensure_parquet_cache(snapshot, None, staging_dir)
            )
            converted = await asyncio.wait_for(operation, timeout=_ENSURE_CACHE_TIMEOUT)
        return converted
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
    # Cached content can be shared by different sources. Bind provenance only
    # in this call's view; never rewrite shared metadata outside its cache lock.
    meta.source_file = abs_path
    file_view = render_xml(
        meta,
        parquet_path=parquet_path,
        original_path=_sandbox_original_path(abs_path, executor.workspace_root),
        related_files=meta.related_files,
    )
    name = Path(abs_path).name
    _register_source(executor, cache, abs_path)
    cache.set_parquet(abs_path, cache_path)
    cache.set_analyzed(abs_path, True)
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

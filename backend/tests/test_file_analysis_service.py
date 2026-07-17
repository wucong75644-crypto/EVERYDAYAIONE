"""file_analysis_service 阶段边界测试。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree import ElementTree

import pandas as pd
import pytest

from services.agent.agent_result import AgentResult
from services.agent.file_analysis_service import (
    _build_analysis_result,
    _convert_to_parquet,
    _register_source,
    _resolve_analysis_path,
    _sandbox_parquet_path,
    _validate_analysis_file,
    analyze_file,
)
from services.agent.file_meta import FileMeta


def _owner() -> SimpleNamespace:
    return SimpleNamespace(
        conversation_id="conv-1",
        org_id="org-1",
        workspace_user_id="workspace-1",
        _ANALYZE_EXTENSIONS={".xlsx", ".xls", ".csv", ".tsv"},
    )


@pytest.mark.asyncio
async def test_analyze_file_success_orchestrates_stages(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("month,sales\n1,10\n", encoding="utf-8")
    expected = AgentResult(summary="ok", status="success")
    cache = MagicMock(_staging_dir=None)
    cache.resolve.return_value = None
    executor = SimpleNamespace(
        workspace_root=str(tmp_path),
        resolve_safe_path=lambda _path: source,
    )
    settings = SimpleNamespace(file_workspace_root=str(tmp_path))

    with patch(
        "services.agent.file_path_cache.get_file_cache",
        return_value=cache,
    ), patch(
        "services.agent.file_analysis_service._convert_to_parquet",
        new=AsyncMock(return_value=(str(tmp_path / "sales.parquet"), None)),
    ), patch(
        "services.agent.file_analysis_service._build_analysis_result",
        return_value=expected,
    ):
        result = await analyze_file(
            _owner(), executor, {"path": str(source)}, settings,
        )

    assert result is expected
    cache.set_staging_dir.assert_called_once()


def test_invalid_file_id_is_retryable():
    result = _resolve_analysis_path(
        _owner(),
        MagicMock(),
        {"file_id": "bad-id"},
        MagicMock(),
    )
    assert isinstance(result, AgentResult)
    assert result.metadata["retryable"] is True
    assert "格式错误" in result.summary


def test_legacy_permission_error_is_not_retryable():
    cache = MagicMock()
    cache.resolve.return_value = None
    executor = MagicMock()
    executor.resolve_safe_path.side_effect = PermissionError("denied")

    result = _resolve_analysis_path(
        _owner(), executor, {"path": "../secret.csv"}, cache,
    )

    assert isinstance(result, AgentResult)
    assert result.metadata["retryable"] is False
    assert "路径不允许" in result.summary


def test_unsupported_extension_is_not_retryable(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("text", encoding="utf-8")

    result = _validate_analysis_file(
        str(source), "notes.txt", {".csv"},
    )

    assert result is not None
    assert result.metadata["retryable"] is False
    assert "仅支持" in result.summary


def test_missing_path_is_retryable():
    result = _resolve_analysis_path(
        _owner(), MagicMock(), {}, MagicMock(),
    )
    assert isinstance(result, AgentResult)
    assert result.metadata["retryable"] is True


def test_not_a_file_is_retryable(tmp_path):
    result = _validate_analysis_file(
        str(tmp_path / "missing.csv"), "missing.csv", {".csv"},
    )
    assert result is not None
    assert result.metadata["retryable"] is True


@pytest.mark.asyncio
async def test_conversion_timeout_registers_source(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("month,sales\n1,10\n", encoding="utf-8")
    cache = MagicMock()

    with patch(
        "services.agent.data_query_cache.ensure_parquet_cache_csv",
        new=AsyncMock(side_effect=TimeoutError),
    ), patch(
        "services.agent.data_query_cache._ENSURE_CACHE_TIMEOUT",
        0.01,
    ):
        result = await _convert_to_parquet(
            SimpleNamespace(workspace_root=str(tmp_path)),
            cache,
            str(source),
            str(tmp_path),
        )

    assert isinstance(result, AgentResult)
    assert result.metadata["error_category"] == "timeout"
    assert result.metadata["retryable"] is True
    cache.register.assert_called()


@pytest.mark.asyncio
async def test_conversion_value_error_is_not_retryable(tmp_path):
    source = tmp_path / "empty.csv"
    source.write_text("", encoding="utf-8")

    with patch(
        "services.agent.data_query_cache.ensure_parquet_cache_csv",
        new=AsyncMock(side_effect=ValueError("空文件")),
    ):
        result = await _convert_to_parquet(
            SimpleNamespace(workspace_root=str(tmp_path)),
            MagicMock(),
            str(source),
            str(tmp_path),
        )

    assert isinstance(result, AgentResult)
    assert result.metadata["retryable"] is False
    assert result.summary == "空文件"


@pytest.mark.asyncio
async def test_csv_conversion_success(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("month,sales\n1,10\n", encoding="utf-8")
    expected = (str(tmp_path / "sales.parquet"), None)

    with patch(
        "services.agent.data_query_cache.ensure_parquet_cache_csv",
        new=AsyncMock(return_value=expected),
    ):
        result = await _convert_to_parquet(
            SimpleNamespace(workspace_root=str(tmp_path)),
            MagicMock(),
            str(source),
            str(tmp_path),
        )

    assert result == expected


def test_build_result_registers_parquet_and_sheets(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("month,sales\n1,10\n", encoding="utf-8")
    parquet = tmp_path / "sales.parquet"
    parquet.write_bytes(b"parquet")
    cache = MagicMock()
    executor = SimpleNamespace(workspace_root=str(tmp_path))
    meta = FileMeta(
        source_file=str(source),
        summary={"row_count": 1, "col_count": 2, "sheet_count": 2},
    )

    with patch(
        "services.agent.file_meta.read_file_meta",
        return_value=meta,
    ):
        result = _build_analysis_result(
            executor,
            cache,
            str(source),
            str(parquet),
            str(tmp_path),
            ["一月", "二月"],
            0.2,
        )

    assert result.status == "success"
    assert "Sheet 列表" in result.summary
    cache.set_parquet.assert_called_once_with("sales.csv", str(parquet))
    cache.set_analyzed.assert_called_once_with("sales.csv", True)


def test_build_result_rejects_missing_metadata(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("month,sales\n1,10\n", encoding="utf-8")
    staging = tmp_path / "staging" / "conv-1"
    staging.mkdir(parents=True)
    parquet = staging / "cache.parquet"
    parquet.write_bytes(b"parquet")
    cache = MagicMock()

    with patch(
        "services.agent.file_meta.read_file_meta",
        return_value=None,
    ):
        result = _build_analysis_result(
            SimpleNamespace(workspace_root=str(tmp_path)),
            cache,
            str(source),
            str(parquet),
            str(staging),
            None,
            0.1,
        )

    assert result.status == "error"
    assert result.error_message == "PARQUET_METADATA_MISSING"
    assert result.metadata["retryable"] is True
    cache.set_parquet.assert_not_called()
    cache.set_analyzed.assert_not_called()


def test_build_result_renders_real_parquet_path_for_simplified_csv_meta(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("month,sales\n1,10\n", encoding="utf-8")
    staging = tmp_path / "staging" / "conv-1"
    staging.mkdir(parents=True)
    parquet = staging / "_cache_v3.0_real_csv.parquet"
    parquet.write_bytes(b"parquet")
    meta = FileMeta(
        source_file=str(source),
        summary={"row_count": 1, "col_count": 2, "sheet_count": 1},
    )

    with patch(
        "services.agent.file_meta.read_file_meta",
        return_value=meta,
    ):
        result = _build_analysis_result(
            SimpleNamespace(workspace_root=str(tmp_path)),
            MagicMock(),
            str(source),
            str(parquet),
            str(staging),
            None,
            0.1,
        )

    expected = "staging/_cache_v3.0_real_csv.parquet"
    assert result.status == "success"
    assert f"<parquet_path>{expected}</parquet_path>" in result.summary
    assert expected in result.summary


def test_build_result_replaces_stale_cached_xml_path(tmp_path):
    source = tmp_path / "report.xlsx"
    source.write_bytes(b"xlsx")
    staging = tmp_path / "staging" / "conv-1"
    staging.mkdir(parents=True)
    parquet = staging / "_cache_v3.0_actual_sheet0.parquet"
    parquet.write_bytes(b"parquet")
    meta = FileMeta(
        source_file=str(source),
        summary={"row_count": 1, "col_count": 1, "sheet_count": 1},
        xml_view="<parquet_path>staging/guessed.parquet</parquet_path>",
    )

    with patch(
        "services.agent.file_meta.read_file_meta",
        return_value=meta,
    ):
        result = _build_analysis_result(
            SimpleNamespace(workspace_root=str(tmp_path)),
            MagicMock(),
            str(source),
            str(parquet),
            str(staging),
            None,
            0.1,
        )

    assert "staging/_cache_v3.0_actual_sheet0.parquet" in result.summary
    assert "staging/guessed.parquet" not in result.summary


def test_build_result_rejects_parquet_outside_current_staging(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("month,sales\n1,10\n", encoding="utf-8")
    staging = tmp_path / "staging" / "conv-1"
    staging.mkdir(parents=True)
    parquet = tmp_path / "other" / "cache.parquet"
    parquet.parent.mkdir()
    parquet.write_bytes(b"parquet")
    cache = MagicMock()

    result = _build_analysis_result(
        SimpleNamespace(workspace_root=str(tmp_path)),
        cache,
        str(source),
        str(parquet),
        str(staging),
        None,
        0.1,
    )

    assert result.status == "error"
    assert result.metadata["retryable"] is True
    cache.set_parquet.assert_not_called()
    cache.set_analyzed.assert_not_called()


def test_sandbox_parquet_path_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        _sandbox_parquet_path(
            str(tmp_path / "staging" / "missing.parquet"),
            str(tmp_path / "staging"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "separator"),
    [(".csv", ","), (".tsv", "\t")],
)
async def test_real_delimited_conversion_returns_executable_stable_path(
    tmp_path, suffix, separator,
):
    from services.agent.data_query_cache import ensure_parquet_cache_csv

    source = tmp_path / f"sales{suffix}"
    source.write_text(
        f"month{separator}sales\n1{separator}10\n",
        encoding="utf-8",
    )
    staging = tmp_path / "staging" / "conv-1"
    staging.mkdir(parents=True)
    executor = SimpleNamespace(workspace_root=str(tmp_path))

    first_cache_path, _ = await ensure_parquet_cache_csv(
        str(source), str(staging),
    )
    first_result = _build_analysis_result(
        executor,
        MagicMock(),
        str(source),
        first_cache_path,
        str(staging),
        None,
        0.1,
    )
    second_cache_path, _ = await ensure_parquet_cache_csv(
        str(source), str(staging),
    )
    second_result = _build_analysis_result(
        executor,
        MagicMock(),
        str(source),
        second_cache_path,
        str(staging),
        None,
        0.1,
    )

    first_xml = ElementTree.fromstring(first_result.summary)
    second_xml = ElementTree.fromstring(second_result.summary)
    first_path = first_xml.findtext("./data_access/parquet_path")
    second_path = second_xml.findtext("./data_access/parquet_path")
    assert first_path == second_path
    assert first_path == f"staging/{Path(first_cache_path).name}"
    sandbox_file = tmp_path / "staging" / "conv-1" / Path(first_path).name
    frame = pd.read_parquet(sandbox_file)
    assert frame.to_dict(orient="records") == [{"month": 1, "sales": 10}]


def test_register_source_ignores_external_relative_path(tmp_path):
    external = tmp_path.parent / "external.csv"
    cache = MagicMock()
    _register_source(
        SimpleNamespace(workspace_root=str(tmp_path)),
        cache,
        str(external),
    )
    cache.register.assert_called_once_with(
        "external.csv", workspace=str(external),
    )

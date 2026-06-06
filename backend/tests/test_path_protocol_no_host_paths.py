"""路径协议合约测试 — 确保 file_analyze 工具输出不暴露 host 绝对路径。

Phase 1 部署后线上事故:file_xml_renderer 把 host 绝对路径
(/mnt/nas-workspace/org/.../staging/.../xxx.parquet) 直接渲染到 LLM 看的
<parquet_path>/<quick_start>/<file_meta><path>/<code_example> 字段。LLM 照搬到
code_execute,在 nsjail 沙盒里 host 路径不可见 → DuckDB "找不到文件"。

事后 sanitizer(grep 字面常量)拦不住变量传入的 host 路径。真正根本修复:
源头规范化 —— 工具内部全程用沙盒相对路径,host 绝对路径不流入 LLM 上下文。

本测试用合约验证: 给 file_analyze 渲染层喂 host 绝对路径输入,断言输出 XML/
markdown 不含 host 路径前缀。守护未来 contributor 不再泄漏 host 路径。
"""

from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.agent.file_meta import FileMeta
from services.agent.file_meta.view import format_file_view
from services.agent.file_xml_renderer import render_xml


# 典型 host 路径前缀(线上事故里出现的实际值)
_HOST_PREFIXES = (
    "/mnt/nas-workspace/",
    "/data/workspace/",
    "/home/",
    "/var/",
)


def _make_meta(source_file: str = "") -> FileMeta:
    """构造最小可用 FileMeta(各 V2 字段填默认空值)"""
    return FileMeta(
        source_file=source_file,
        summary={"row_count": 100, "col_count": 5, "sheet_count": 1},
        schema={
            "店铺": {"col": "A", "type": "string", "null_ratio": 0.0, "unique": 50}
        },
        sample={"head": [{"店铺": "测试店铺"}]},
        ai_decision={
            "model_used": "qwen-turbo",
            "elapsed_ms": 100,
            "header_row": 1,
            "data_start_row": 2,
            "column_semantics": [
                {"col_letter": "A", "business_name": "店铺", "type": "name"}
            ],
            "regions": [{"id": "1", "range": "A1:E100", "role": "primary"}],
            "sheets": [{"name": "Sheet1", "role": "data"}],
            "overall_summary": "测试数据",
        },
        cleaning_strategy={},
        processed_at="2026-06-06T18:38:31",
    )


class TestRenderXmlSourceLevelPathProtocol:
    """source-level 合约: render_xml 接收什么路径就渲染什么路径"""

    def test_relative_paths_rendered_as_is(self):
        """传相对路径 → XML 里就是相对路径(正常路径)"""
        meta = _make_meta(source_file="/mnt/nas-workspace/org/x/u/上传/2026-06/a.xlsx")
        xml = render_xml(
            meta,
            parquet_path="staging/a.parquet",
            original_path="上传/2026-06/a.xlsx",
        )
        # parquet_path 字段必须是相对路径
        assert "<parquet_path>staging/a.parquet</parquet_path>" in xml
        # quick_start CDATA 里也是相对路径
        assert "read_parquet('staging/a.parquet')" in xml
        # file_meta path 必须是 caller 传的相对路径,不被 meta.source_file 覆盖
        assert "<path>上传/2026-06/a.xlsx</path>" in xml

    def test_no_host_prefix_when_caller_passes_relative(self):
        """关键合约: 即便 meta.source_file 是 host 路径,只要 caller 传相对路径,
        渲染出的 XML 就不应出现任何 host 前缀。"""
        meta = _make_meta(
            source_file="/mnt/nas-workspace/org/eadc4c11/user-xxx/上传/2026-06/a.xlsx"
        )
        xml = render_xml(
            meta,
            parquet_path="staging/a.parquet",
            original_path="上传/2026-06/a.xlsx",
        )
        for prefix in _HOST_PREFIXES:
            assert prefix not in xml, (
                f"XML 包含 host 路径前缀 {prefix!r},LLM 看到会照搬到沙盒导致路径错误"
            )


class TestFormatFileViewMarkdownFallback:
    """markdown 降级路径同样不应泄漏 host 路径"""

    def test_markdown_only_shows_basename(self):
        """format_file_view (markdown fallback) 只显示 basename"""
        meta = _make_meta(
            source_file="/mnt/nas-workspace/org/x/u/上传/2026-06/销售明细.xlsx"
        )
        view = format_file_view(meta)
        # 文件名出现
        assert "销售明细.xlsx" in view
        # host 路径前缀不出现
        for prefix in _HOST_PREFIXES:
            assert prefix not in view, (
                f"markdown 视图包含 host 前缀 {prefix!r}"
            )


class TestEnrichMetaIntegration:
    """e2e 合约: _enrich_meta_v2 调用链不应让 host 路径流入 xml_view"""

    def test_enrich_pipeline_strips_host_paths(self, tmp_path, monkeypatch):
        """模拟生产调用:_enrich_meta_v2 传入 host 路径,验证 meta.xml_view 是相对路径"""
        from services.agent.data_query_cache import _enrich_meta_v2
        from services.agent.file_meta import write_file_meta
        from dataclasses import dataclass

        # 模拟 host 工作区结构: workspace_dir/staging/{conv}/
        workspace_dir = tmp_path / "org" / "test-org" / "test-user"
        staging_dir = workspace_dir / "staging" / "test-conv"
        upload_dir = workspace_dir / "上传" / "2026-06"
        staging_dir.mkdir(parents=True)
        upload_dir.mkdir(parents=True)

        # 模拟 host 绝对路径
        excel_host = upload_dir / "销售.xlsx"
        excel_host.write_bytes(b"fake")
        cache_host = staging_dir / "_cache_abc.parquet"
        cache_host.write_bytes(b"fake parquet")

        # 写入初始 meta(模拟 to_parquet 后的状态)
        meta = _make_meta(source_file=str(excel_host))
        write_file_meta(str(cache_host), meta)

        # 模拟 _enrich_meta_v2 的 decision/strategy 参数
        @dataclass
        class _D:
            pass
        decision, strategy = _D(), _D()

        _enrich_meta_v2(
            cache_path=str(cache_host),
            excel_path=str(excel_host),
            decision=decision,
            strategy=strategy,
            staging_dir=str(staging_dir),
        )

        # 重新读 meta,验证 xml_view 不含 host 路径
        from services.agent.file_meta import read_file_meta
        meta_after = read_file_meta(str(cache_host))
        xml = meta_after.xml_view if meta_after else ""

        assert xml, "xml_view 应该被生成"
        # 相对路径出现
        assert "staging/_cache_abc.parquet" in xml
        assert "上传/2026-06/销售.xlsx" in xml
        # host 路径不出现(关键合约)
        assert str(tmp_path) not in xml, (
            f"xml_view 泄漏 host 路径 {tmp_path}:\n{xml[:500]}"
        )

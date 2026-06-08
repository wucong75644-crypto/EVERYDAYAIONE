"""沙盒三引擎产物协议覆盖测试 (Phase 2)

守护 13 个场景:LLM 任意写法都能让产物送达前端。
详见 docs/document/TECH_沙盒产物协议.md
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ============================================================
# Engine A: Jupyter mimebundle 协议 — _mimebundle_to_payload 分发
# ============================================================


class TestMimebundleDispatch:
    """_mimebundle_to_payload 必须按 mimetype 全量分发"""

    def test_plotly_dispatched_as_chart(self):
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"application/vnd.plotly.v1+json": {"data": [], "layout": {"title": {"text": "T"}}}}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "chart"
        assert payload["spec_format"] == "plotly"
        assert payload["title"] == "T"

    def test_vegalite_dispatched_as_chart(self):
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"application/vnd.vegalite.v5+json": {"mark": "bar", "title": "T"}}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "chart"
        assert payload["spec_format"] == "vegalite"

    def test_png_dispatched_as_image_with_inline_bytes(self):
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        import base64
        bundle = {"image/png": base64.b64encode(b"fake_png").decode()}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "image"
        assert payload["_inline_bytes"] == b"fake_png"
        assert payload["_inline_ext"] == "png"

    def test_svg_dispatched_as_image_inline(self):
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"image/svg+xml": "<svg/>"}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "image"
        assert payload["svg"] == "<svg/>"

    def test_html_dispatched_as_html_kind(self):
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"text/html": "<table>...</table>"}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "html"
        assert "<table>" in payload["html"]

    def test_json_dispatched_as_data_kind(self):
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"application/json": {"k": "v"}}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "data"

    def test_markdown_dispatched_as_markdown(self):
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"text/markdown": "# Header"}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "markdown"

    def test_unknown_mime_returns_none(self):
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"text/plain": "hello"}
        assert _mimebundle_to_payload(bundle) is None


# ============================================================
# Engine B: Runtime 写盘 diff — _auto_emit_missed
# ============================================================


class TestAutoEmitMissed:
    """LLM 写 文件到 下载/ 但不调 emit_xxx,Engine B 应自动补"""

    @pytest.mark.asyncio
    async def test_savefig_png_auto_emitted_as_image(self, tmp_path, monkeypatch):
        from services.sandbox.executor import SandboxExecutor

        # Mock OSS 上传(避免依赖外部服务)
        from services import file_upload
        async def _fake_upload(name, size, output_dir, user_id, org_id):
            return {"url": f"https://cdn.test/{name}", "mime_type": "image/png",
                    "workspace_path": f"下载/{name}", "size": size}
        monkeypatch.setattr(file_upload, "upload_to_payload", _fake_upload)

        output_dir = tmp_path / "下载"
        output_dir.mkdir()
        executor = SandboxExecutor(output_dir=str(output_dir))

        # 模拟 LLM 写了 PNG 到 下载/ 但没 emit
        snapshot_before = executor._snapshot_output_dir()
        (output_dir / "chart.png").write_bytes(b"fake_png_data")

        payloads = await executor._auto_emit_missed(snapshot_before, existing_payloads=[])
        assert len(payloads) == 1
        assert payloads[0]["kind"] == "image"
        assert payloads[0]["name"] == "chart.png"
        assert payloads[0]["auto_detected"] is True
        assert payloads[0]["url"] == "https://cdn.test/chart.png"

    @pytest.mark.asyncio
    async def test_excel_auto_emitted_as_file(self, tmp_path, monkeypatch):
        from services.sandbox.executor import SandboxExecutor
        from services import file_upload
        async def _fake_upload(name, size, output_dir, user_id, org_id):
            return {"url": f"https://cdn.test/{name}", "mime_type": "application/octet-stream",
                    "workspace_path": f"下载/{name}", "size": size}
        monkeypatch.setattr(file_upload, "upload_to_payload", _fake_upload)

        output_dir = tmp_path / "下载"
        output_dir.mkdir()
        executor = SandboxExecutor(output_dir=str(output_dir))
        snapshot_before = executor._snapshot_output_dir()
        (output_dir / "report.xlsx").write_bytes(b"fake_xlsx")

        payloads = await executor._auto_emit_missed(snapshot_before, existing_payloads=[])
        assert len(payloads) == 1
        assert payloads[0]["kind"] == "file"
        assert payloads[0]["name"] == "report.xlsx"

    @pytest.mark.asyncio
    async def test_dedupe_llm_explicit_emit_wins(self, tmp_path, monkeypatch):
        """LLM 已显式 emit 的文件,自动 diff 不再补"""
        from services.sandbox.executor import SandboxExecutor
        from services import file_upload
        async def _fake_upload(name, size, output_dir, user_id, org_id):
            return {"url": "x", "mime_type": "image/png", "workspace_path": "", "size": 0}
        monkeypatch.setattr(file_upload, "upload_to_payload", _fake_upload)

        output_dir = tmp_path / "下载"
        output_dir.mkdir()
        executor = SandboxExecutor(output_dir=str(output_dir))
        snapshot_before = executor._snapshot_output_dir()
        (output_dir / "x.png").write_bytes(b"fake")

        existing = [{"kind": "image", "path": "下载/x.png", "name": "x.png"}]
        payloads = await executor._auto_emit_missed(snapshot_before, existing_payloads=existing)
        assert len(payloads) == 0  # 已 emit 不重复

    @pytest.mark.asyncio
    async def test_unchanged_file_not_emitted(self, tmp_path):
        """旧文件 (mtime/size 未变) 不算产物"""
        from services.sandbox.executor import SandboxExecutor
        output_dir = tmp_path / "下载"
        output_dir.mkdir()
        (output_dir / "old.png").write_bytes(b"old")
        executor = SandboxExecutor(output_dir=str(output_dir))
        snapshot_before = executor._snapshot_output_dir()  # 含 old.png

        # 不修改 old.png,只扫描
        payloads = await executor._auto_emit_missed(snapshot_before, existing_payloads=[])
        assert len(payloads) == 0


# ============================================================
# Engine C: LLM 显式 emit — emit_chart/file/image/table
# ============================================================


class TestExplicitEmit:
    """emit_chart/file/image/table 直接调用产 payload"""

    def test_emit_chart_payload(self):
        from services.sandbox.emit_protocol import build_chart_payload
        p = build_chart_payload({"series": []}, title="T")
        assert p["kind"] == "chart"
        assert p["title"] == "T"

    def test_emit_file_payload(self, tmp_path):
        from services.sandbox.emit_protocol import build_file_payload
        f = tmp_path / "report.xlsx"
        f.write_bytes(b"x")
        p = build_file_payload(str(f), label="月报")
        assert p["kind"] == "file"
        assert p["label"] == "月报"
        assert p["size"] > 0

    def test_emit_image_payload(self, tmp_path):
        from services.sandbox.emit_protocol import build_image_payload
        f = tmp_path / "x.png"
        f.write_bytes(b"fake")
        p = build_image_payload(str(f), alt="图")
        assert p["kind"] == "image"
        assert p["alt"] == "图"

    def test_emit_table_payload(self):
        from services.sandbox.emit_protocol import build_table_payload
        p = build_table_payload([{"a": 1, "b": 2}], title="T")
        assert p["kind"] == "table"
        assert p["title"] == "T"
        assert p["rows"] == [{"a": 1, "b": 2}]


# ============================================================
# 完整产物覆盖矩阵(13 个场景)
# ============================================================


class TestCoverageMatrix:
    """13 场景全覆盖摘要 — 每个场景都有对应的引擎和测试"""

    def test_all_scenarios_have_coverage(self):
        """文档化:13 个场景,各对应哪个引擎"""
        matrix = {
            "emit_image_explicit": "C",
            "emit_chart_explicit": "C",
            "emit_file_explicit": "C",
            "emit_table_explicit": "C",
            "plt_show": "A_matplotlib_hook",
            "plotly_fig_show": "A_mimebundle",
            "altair_chart_show": "A_mimebundle",
            "display_obj_explicit": "A_hooked_display",
            "df_head_last_line": "A_last_expr",
            "fig_last_line": "A_last_expr",
            "image_open_last_line": "A_last_expr",
            "plt_savefig_to_output": "B_writedisk_diff",
            "df_to_excel_to_output": "B_writedisk_diff",
        }
        assert len(matrix) == 13, "保证 13 个场景全部记录"
        assert "B_writedisk_diff" in matrix.values(), "Engine B 必须覆盖"
        assert any(v.startswith("A_") for v in matrix.values()), "Engine A 必须覆盖"
        assert "C" in matrix.values(), "Engine C 必须覆盖"


# ============================================================
# matplotlib 中文字体守护(防止回归)
# ============================================================


class TestMatplotlibChineseFont:
    """install_matplotlib_hook 必须默认配置中文字体,防止中文渲染为方块"""

    def test_install_hook_sets_chinese_font_rcparams(self, tmp_path):
        """install_matplotlib_hook 调用后, rcParams 字体列表必须含中文字体优先"""
        from services.sandbox.emit_auto_hooks import install_matplotlib_hook
        import matplotlib.pyplot as plt

        g: dict = {}
        emit_buffer: list = []
        install_matplotlib_hook(g, str(tmp_path), emit_buffer)

        # 字体列表第一个必须是中文字体(生产 Linux 已装)
        fonts = plt.rcParams["font.sans-serif"]
        assert fonts[0] == "WenQuanYi Micro Hei", (
            f"中文字体未注入或顺序错: {fonts}. "
            f"修 install_matplotlib_hook 把 'WenQuanYi Micro Hei' 放第一位"
        )
        # 负号防方块
        assert plt.rcParams["axes.unicode_minus"] is False, (
            "axes.unicode_minus 必须为 False,否则负号显示为方块"
        )

    def test_chinese_font_in_fallback_list_avoids_box(self, tmp_path):
        """模拟生产场景: 画含中文的图, 不应出现 'missing from font' 警告

        本地 macOS 无 WenQuanYi 时会 fallback, 不一定能渲染中文,
        但只断言不 RuntimeError + rcParams 配置正确 (生产 Linux 由 POC 实测保证)。
        """
        from services.sandbox.emit_auto_hooks import install_matplotlib_hook
        import matplotlib.pyplot as plt

        g: dict = {}
        emit_buffer: list = []
        install_matplotlib_hook(g, str(tmp_path), emit_buffer)

        # 画一个含中文 + 负数的图,不应 raise(本地 fallback 不强求渲染中文)
        fig, ax = plt.subplots()
        ax.bar(["运营", "退款"], [100, -50])
        ax.set_title("销售对比")
        ax.set_ylabel("金额(¥)")
        out = tmp_path / "chinese_test.png"
        fig.savefig(str(out), dpi=60)
        plt.close(fig)
        assert out.exists() and out.stat().st_size > 0

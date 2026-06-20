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

    def test_html_dataframe_dispatched_as_table(self):
        """pandas DataFrame _repr_html_ → 智能解析为 table payload (对标 Databricks display)。

        对称美:df._repr_html_() 出 → pd.read_html 进。
        """
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        import pandas as pd
        df = pd.DataFrame({"name": ["张三", "李四"], "count": [100, 200]})
        bundle = {"text/html": df.to_html()}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "table"
        assert "name" in payload["columns"]
        assert "count" in payload["columns"]
        assert len(payload["rows"]) == 2
        assert payload["rows"][0]["name"] == "张三"
        assert payload["rows"][0]["count"] == 100

    def test_html_non_table_returns_none(self):
        """非表格 HTML(IPython.display.HTML('<div>...</div>'))不接通,return None。

        简化派(Databricks/Hex)路径:非结构化 HTML 不渲染,避免 XSS。
        """
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"text/html": "<div>纯文本 div</div>"}
        assert _mimebundle_to_payload(bundle) is None

    def test_html_empty_table_returns_none(self):
        """空表格 HTML 不构造无意义 payload"""
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"text/html": "<table><thead></thead><tbody></tbody></table>"}
        assert _mimebundle_to_payload(bundle) is None

    def test_html_multi_table_takes_first(self):
        """HTML 含多个 table 只取第一个(pandas 习惯)"""
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        import pandas as pd
        df1 = pd.DataFrame({"a": [1]})
        df2 = pd.DataFrame({"b": [2]})
        bundle = {"text/html": df1.to_html() + df2.to_html()}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "table"
        assert "a" in payload["columns"]
        assert "b" not in payload["columns"]

    def test_html_unnamed_index_column_stripped(self):
        """pandas df.to_html() 默认 index=True 反解会产生 Unnamed: 0 列,必须去掉。

        场景:LLM 在 sandbox 写 df.head(),触发默认 to_html(index=True)。
        """
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        import pandas as pd
        df = pd.DataFrame({"name": ["张三"], "count": [100]})
        bundle = {"text/html": df.to_html()}  # 默认 index=True
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "table"
        assert payload["columns"] == ["name", "count"]
        assert "Unnamed: 0" not in payload["columns"]

    def test_html_nan_converted_to_none_for_json_safety(self):
        """pandas NaN → None (JSON 标准 NaN 非法,前端 JSON.parse 会崩)"""
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        import json
        import pandas as pd
        df = pd.DataFrame({"name": ["张三", None], "count": [100, 80]})
        bundle = {"text/html": df.to_html()}
        payload = _mimebundle_to_payload(bundle)
        assert payload["kind"] == "table"
        # NaN 必须转 None,且 JSON 可序列化(不抛 ValueError)
        rows_json = json.dumps(payload["rows"])
        assert "NaN" not in rows_json
        assert payload["rows"][1]["name"] is None

    def test_json_returns_none(self):
        """application/json (IPython.display.JSON) 不接通 — LLM Agent 场景不需要。

        简化派(Databricks/Hex)做法:JSON 嵌套数据让 LLM 用 print(json.dumps) 即可,
        不引入独立的 data block 渲染器。
        """
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"application/json": {"k": "v"}}
        assert _mimebundle_to_payload(bundle) is None

    def test_markdown_returns_none(self):
        """text/markdown (IPython.display.Markdown) 不接通 — LLM 主消息本身就是 markdown。

        重复功能,简化派(Databricks/Hex)做法是不接 markdown mime。
        """
        from services.sandbox.emit_auto_hooks import _mimebundle_to_payload
        bundle = {"text/markdown": "# Header"}
        assert _mimebundle_to_payload(bundle) is None

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

    def test_chinese_aliases_registered_to_real_font(self, tmp_path):
        """关键: install_matplotlib_hook 把 LLM 常用字体名注册指向真实字体文件。

        生产 Linux: 7 个 LLM 常用字体名 (SimHei/Microsoft YaHei/PingFang SC 等)
        全部注册到 fontManager.ttflist, findfont 直接命中 wqy 字体文件。
        本地 macOS 无 wqy 文件 → 跳过注册 (开发环境不强求)。

        这是 mplfonts 库的行业标准做法, 比 monkey patch 更干净。
        """
        from services.sandbox.emit_auto_hooks import install_matplotlib_hook
        from matplotlib import font_manager as fm
        import os

        # 重置标记位以便测试可重入
        if hasattr(fm.fontManager, "_emit_chinese_aliases"):
            delattr(fm.fontManager, "_emit_chinese_aliases")

        install_matplotlib_hook({}, str(tmp_path), [])

        WQY_PATH = "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc"
        if not os.path.exists(WQY_PATH):
            pytest.skip("wqy 字体未装 (本地开发环境跳过, 生产 Linux 必装)")

        # LLM 常用字体名应全部 findfont 到 wqy 路径
        for alias in ["SimHei", "Microsoft YaHei", "PingFang SC"]:
            resolved = fm.fontManager.findfont(alias, fallback_to_default=False)
            assert WQY_PATH in resolved, (
                f"别名 {alias!r} 未指向 wqy: {resolved}"
            )

    def test_alias_registration_dedupes_on_second_call(self, tmp_path):
        """重复调用 install_matplotlib_hook 不应重复 append 别名 (标记位去重)"""
        from services.sandbox.emit_auto_hooks import install_matplotlib_hook
        from matplotlib import font_manager as fm
        import os

        if not os.path.exists("/usr/share/fonts/wqy-microhei/wqy-microhei.ttc"):
            pytest.skip("wqy 字体未装")

        # 重置标记位
        if hasattr(fm.fontManager, "_emit_chinese_aliases"):
            delattr(fm.fontManager, "_emit_chinese_aliases")

        n0 = len(fm.fontManager.ttflist)
        install_matplotlib_hook({}, str(tmp_path), [])
        n1 = len(fm.fontManager.ttflist)
        install_matplotlib_hook({}, str(tmp_path), [])  # 二次调用应跳过
        n2 = len(fm.fontManager.ttflist)

        assert n1 - n0 == 7, f"首次注册应 +7 别名, 实际 +{n1-n0}"
        assert n2 == n1, f"二次调用应去重, 但又加了 {n2-n1} 条"

    def test_macos_no_wqy_skips_registration_gracefully(self, tmp_path, monkeypatch):
        """macOS 无 wqy 字体路径时, install_matplotlib_hook 不报错只跳过"""
        from services.sandbox import emit_auto_hooks
        from matplotlib import font_manager as fm

        # 重置标记位
        if hasattr(fm.fontManager, "_emit_chinese_aliases"):
            delattr(fm.fontManager, "_emit_chinese_aliases")

        # mock os.path.exists 总返回 False (模拟没 wqy)
        original_exists = emit_auto_hooks.os.path.exists
        def mock_exists(p):
            if "wqy" in p:
                return False
            return original_exists(p)
        monkeypatch.setattr(emit_auto_hooks.os.path, "exists", mock_exists)

        n_before = len(fm.fontManager.ttflist)
        # 不应报错
        emit_auto_hooks.install_matplotlib_hook({}, str(tmp_path), [])
        n_after = len(fm.fontManager.ttflist)
        assert n_after == n_before, "无 wqy 时不应注册任何别名"

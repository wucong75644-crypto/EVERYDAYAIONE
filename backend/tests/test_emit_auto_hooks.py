"""自动 hook 守护测试 — matplotlib + plotly/altair fake IPython.display

确保沙盒注入的 fake IPython.display.publish_display_data 能识别 plotly/altair
的 mime bundle 自动转 emit_chart;matplotlib hook 让 plt.show() 自动 emit_image。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest


@pytest.fixture(autouse=True)
def _cleanup_sys_modules():
    """每个测试结束清理 fake IPython 模块,避免污染下一个测试"""
    yield
    sys.modules.pop("IPython", None)
    sys.modules.pop("IPython.display", None)


class TestIPythonDisplayShim:
    """fake IPython.display.publish_display_data 自动转 emit_chart"""

    def test_install_creates_modules(self):
        from services.sandbox.emit_auto_hooks import install_ipython_display_shim
        g: dict = {}
        buf: list = []
        install_ipython_display_shim(g, buf)
        assert "IPython.display" in sys.modules
        assert "IPython" in sys.modules
        from IPython.display import publish_display_data, display  # noqa: F401

    def test_plotly_mime_to_emit_chart(self):
        from services.sandbox.emit_auto_hooks import install_ipython_display_shim
        g: dict = {}
        buf: list = []
        install_ipython_display_shim(g, buf)

        from IPython.display import publish_display_data
        plotly_spec = {
            "data": [{"type": "bar", "x": ["A"], "y": [1]}],
            "layout": {"title": {"text": "销售"}},
        }
        publish_display_data({"application/vnd.plotly.v1+json": plotly_spec})

        assert len(buf) == 1
        assert buf[0]["kind"] == "chart"
        assert buf[0]["spec_format"] == "plotly"
        assert buf[0]["title"] == "销售"

    def test_vegalite_mime_to_emit_chart(self):
        from services.sandbox.emit_auto_hooks import install_ipython_display_shim
        g: dict = {}
        buf: list = []
        install_ipython_display_shim(g, buf)

        from IPython.display import publish_display_data
        vega_spec = {"mark": "bar", "encoding": {}}
        publish_display_data({"application/vnd.vegalite.v6+json": vega_spec})

        assert len(buf) == 1
        assert buf[0]["kind"] == "chart"
        assert buf[0]["spec_format"] == "vegalite"

    def test_unknown_mime_ignored(self):
        from services.sandbox.emit_auto_hooks import install_ipython_display_shim
        g: dict = {}
        buf: list = []
        install_ipython_display_shim(g, buf)

        from IPython.display import publish_display_data
        publish_display_data({"text/plain": "hi"})  # 我们暂时不接 text/plain
        assert len(buf) == 0

    def test_display_with_repr_mimebundle(self):
        """display(obj) 调用对象的 _repr_mimebundle_ 自动 emit"""
        from services.sandbox.emit_auto_hooks import install_ipython_display_shim
        g: dict = {}
        buf: list = []
        install_ipython_display_shim(g, buf)

        from IPython.display import display

        class FakePlotlyFig:
            def _repr_mimebundle_(self, include=None, exclude=None):
                return {"application/vnd.plotly.v1+json": {"data": [], "layout": {}}}

        display(FakePlotlyFig())
        assert len(buf) == 1
        assert buf[0]["spec_format"] == "plotly"

    def test_idempotent_install(self):
        """重复 install 不报错(同一 kernel 进程多次执行)"""
        from services.sandbox.emit_auto_hooks import install_ipython_display_shim
        g: dict = {}
        buf: list = []
        install_ipython_display_shim(g, buf)
        install_ipython_display_shim(g, buf)  # 重复
        assert "IPython.display" in sys.modules


class TestMatplotlibHook:
    """plt.show() 自动 savefig + emit_image"""

    def test_plt_show_auto_emit_image(self, tmp_path):
        from services.sandbox.emit_auto_hooks import install_matplotlib_hook

        g: dict = {}
        buf: list = []
        output_dir = str(tmp_path / "下载")
        os.makedirs(output_dir)

        install_matplotlib_hook(g, output_dir, buf)

        # 用户代码模拟
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        ax.bar(["A", "B"], [1, 2])
        plt.show()

        # 应该 emit 至少 1 个 image
        image_emits = [b for b in buf if b.get("kind") == "image"]
        assert len(image_emits) >= 1
        # 文件真实落盘
        assert os.path.exists(image_emits[0]["path"])
        plt.close("all")

    def test_no_figure_no_emit(self, tmp_path):
        """没有活动 figure 时 plt.show() 不产生 emit"""
        from services.sandbox.emit_auto_hooks import install_matplotlib_hook

        g: dict = {}
        buf: list = []
        output_dir = str(tmp_path / "下载")
        os.makedirs(output_dir)

        install_matplotlib_hook(g, output_dir, buf)

        import matplotlib.pyplot as plt
        plt.close("all")
        plt.show()

        assert len(buf) == 0

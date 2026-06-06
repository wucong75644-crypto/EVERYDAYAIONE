"""沙盒生态库自动 hook — plt.show() / plotly fig.show() / altair Chart 无感 emit

参考行业(Plotly/Altair/matplotlib_inline)做法:
  - 沙盒里**注入 fake IPython.display 模块**(20 行),plotly/altair 通过
    `_repr_mimebundle_` 自动触发 publish_display_data → 我们的 emit_chart
  - matplotlib 重写 backend.show:遍历活动 figure savefig + emit_image
  - LLM 不显式调 emit 也能自动渲染(行业标杆 matplotlib_inline 同款 post_execute)

设计文档:docs/document/TECH_沙盒IO统一协议.md
"""
from __future__ import annotations

import os
import sys
import types
from typing import Any


def install_ipython_display_shim(sandbox_globals: dict, emit_buffer: list[dict]) -> None:
    """注入 fake IPython.display 模块,让 plotly/altair 自动 emit_chart

    plotly figure._repr_mimebundle_() 返回 {"application/vnd.plotly.v1+json": {...}}
    altair Chart._repr_mimebundle_() 返回 {"application/vnd.vegalite.v6+json": {...}}
    IPython.display.publish_display_data 是它们 emit 的最终出口。
    我们在 sandbox 里 fake 这个模块,让 publish_display_data 转发到 emit_buffer。
    """
    if "IPython.display" in sys.modules:
        # 已注入(同一 kernel 进程多次执行)不重复
        return

    fake_display = types.ModuleType("IPython.display")
    fake_ipython = types.ModuleType("IPython")

    def publish_display_data(data: dict, metadata: dict | None = None, **_kw: Any) -> None:
        """plotly/altair 通过 _repr_mimebundle_ 调用,我们拦截转 emit_chart"""
        if not isinstance(data, dict):
            return
        # Plotly v1
        plotly_spec = data.get("application/vnd.plotly.v1+json")
        if plotly_spec:
            title = ""
            try:
                title = (plotly_spec.get("layout") or {}).get("title", {}).get("text", "")
            except Exception:
                pass
            emit_buffer.append({
                "kind": "chart",
                "spec_format": "plotly",
                "title": title or "",
                "option": plotly_spec,
            })
            return
        # Vega-Lite (altair) v3+
        for k, v in data.items():
            if k.startswith("application/vnd.vegalite"):
                emit_buffer.append({
                    "kind": "chart",
                    "spec_format": "vegalite",
                    "title": v.get("title", "") if isinstance(v, dict) else "",
                    "option": v,
                })
                return
        # 兜底:image/png 也接(matplotlib_inline 等)
        png = data.get("image/png")
        if png:
            # png 是 bytes,我们暂时不处理 inline base64(交给 matplotlib hook 显式 emit_image)
            pass

    def display(*objs: Any, raw: bool = False, **_kw: Any) -> None:
        """display() 接口:遍历对象,优先调 _repr_mimebundle_"""
        for obj in objs:
            if raw and isinstance(obj, dict):
                publish_display_data(obj)
                continue
            mimebundle_fn = getattr(obj, "_repr_mimebundle_", None)
            if callable(mimebundle_fn):
                try:
                    result = mimebundle_fn()
                    if isinstance(result, tuple) and len(result) >= 1:
                        bundle = result[0]
                    else:
                        bundle = result
                    if isinstance(bundle, dict):
                        publish_display_data(bundle)
                except Exception:
                    pass

    fake_display.publish_display_data = publish_display_data
    fake_display.display = display
    fake_ipython.display = fake_display
    sys.modules["IPython"] = fake_ipython
    sys.modules["IPython.display"] = fake_display


def install_matplotlib_hook(sandbox_globals: dict, output_dir: str, emit_buffer: list[dict]) -> None:
    """重写 matplotlib.pyplot.show 让 plt.show() 自动 savefig + emit_image

    沿用 matplotlib_inline 范本思想:遍历 Gcf 活动 figure,savefig 到下载/,
    然后 emit_image。LLM 不需要显式 plt.savefig + emit_image。
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # 非交互后端(沙盒无显示)
        import matplotlib.pyplot as plt
        from matplotlib._pylab_helpers import Gcf

        _counter = {"n": 0}

        def _hooked_show(*_args: Any, **_kw: Any) -> None:
            for manager in Gcf.get_all_fig_managers():
                fig = manager.canvas.figure
                _counter["n"] += 1
                fname = f"matplotlib_{_counter['n']}.png"
                fpath = os.path.join(output_dir, fname)
                try:
                    os.makedirs(output_dir, exist_ok=True)
                    fig.savefig(fpath, format="png", bbox_inches="tight", dpi=100)
                    if os.path.exists(fpath):
                        emit_buffer.append({
                            "kind": "image",
                            "path": fpath,  # 绝对路径,主进程上传 OSS
                            "alt": fname,
                            "name": fname,
                        })
                except Exception:
                    pass
            # 不关闭 figure,跟 Jupyter inline 行为一致(后续可再 emit)

        plt.show = _hooked_show  # type: ignore[assignment]
        # 同时 patch Figure.show(plotly_show 等场景)
        try:
            from matplotlib.figure import Figure
            def _figure_show(self: Any, *_a: Any, **_kw: Any) -> None:
                _hooked_show()
            Figure.show = _figure_show  # type: ignore[assignment]
        except Exception:
            pass
    except ImportError:
        pass  # matplotlib 未安装,跳过(沙盒环境一般预装,但安全起见)

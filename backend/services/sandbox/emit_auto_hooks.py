"""沙盒 emit 自动 hook — 用真 IPython kernel,行业标准方案

行业对标:
  - OpenAI Code Interpreter:真 Jupyter kernel + ipykernel
  - 我们的实现:真 IPython 包(已 pip install),hook publish_display_data 拦截
  - 替代之前的 fake IPython.display shim(永远补不完 IPython 内部属性)

工作原理:
  plotly fig.show() → IPython.display.publish_display_data → 我们的 hook → emit
  altair Chart._repr_mimebundle_() → IPython.display.display → publish → emit
  matplotlib plt.show() → 我们的 _hooked_show → savefig + emit_image

设计文档: docs/document/TECH_沙盒IO统一协议.md
"""
from __future__ import annotations

import os
from typing import Any


def install_ipython_display_shim(sandbox_globals: dict, emit_buffer: list[dict]) -> None:
    """Hook 真 IPython.display.publish_display_data,转发 plotly/altair mimebundle 到 emit。

    注:真 IPython 包必须已 pip install(requirements.txt 已声明)。
    不再 fake IPython 模块,避免 fake shim 补不完 matplotlib 内部访问的
    IPython.get_ipython / IPython.version_info 等属性。
    """
    try:
        import IPython.display as ip_display
    except ImportError:
        # 极端情况:沙盒环境没装 IPython,跳过自动 hook
        # LLM 仍可显式 emit_chart/emit_image,只是 plt.show()/fig.show() 不自动 emit
        return

    # 已 hook 过的标记(同一 kernel 进程多次执行)
    if getattr(ip_display.publish_display_data, "_emit_hooked", False):
        return

    _orig_publish = ip_display.publish_display_data

    def _hooked_publish(
        data: dict,
        metadata: dict | None = None,
        source: Any = None,
        *,
        transient: dict | None = None,
        update: bool = False,
        **kwargs: Any,
    ) -> None:
        """拦截 publish_display_data 把 mimebundle 转 emit_buffer"""
        if isinstance(data, dict):
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
                return  # 不再调原始 publish(避免双重渲染)

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

        # 不是图表类型,落回原 publish(打 stdout 等无害)
        try:
            _orig_publish(data, metadata, source, transient=transient, update=update, **kwargs)
        except Exception:
            pass

    _hooked_publish._emit_hooked = True  # type: ignore[attr-defined]
    ip_display.publish_display_data = _hooked_publish

    # plotly: 直接 hook Figure.show / pio.show 用 fig.to_dict() 构造 plotly mimebundle。
    # 不用 _repr_mimebundle_(plotly 5.x 默认返回空 dict, 要手动构造)。
    try:
        import plotly.io as pio
        import plotly.basedatatypes as _pbase

        def _plotly_show(fig: Any, *_a: Any, **_kw: Any) -> None:
            try:
                fig_dict = fig.to_dict()
                bundle = {
                    "application/vnd.plotly.v1+json": {
                        "data": fig_dict.get("data", []),
                        "layout": fig_dict.get("layout", {}),
                        "config": {"plotlyServerURL": "https://plot.ly"},
                    }
                }
                _hooked_publish(bundle)
            except Exception:
                pass

        _pbase.BaseFigure.show = _plotly_show  # type: ignore[assignment]
        pio.show = _plotly_show  # type: ignore[assignment]
    except Exception:
        pass

    # altair: hook Chart 类的 show 方法,用 chart.to_dict() 构造 vega-lite mimebundle。
    try:
        import altair as alt
        if not getattr(alt.Chart, "_emit_hooked", False):
            # altair 5.x mimetype: application/vnd.vegalite.v5+json
            _vega_mime = "application/vnd.vegalite.v5+json"
            try:
                _vega_ver = alt.SCHEMA_VERSION.split(".")[0].lstrip("v")
                _vega_mime = f"application/vnd.vegalite.{_vega_ver}+json"
            except Exception:
                pass

            def _altair_show(self: Any, *_a: Any, **_kw: Any) -> None:
                try:
                    spec = self.to_dict()
                    _hooked_publish({_vega_mime: spec})
                except Exception:
                    pass

            alt.Chart.show = _altair_show  # type: ignore[assignment]
            alt.Chart._emit_hooked = True  # type: ignore[attr-defined]
    except Exception:
        pass


def install_matplotlib_hook(sandbox_globals: dict, output_dir: str, emit_buffer: list[dict]) -> None:
    """重写 matplotlib.pyplot.show 让 plt.show() 自动 savefig + emit_image。

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
                            "path": fpath,
                            "alt": fname,
                            "name": fname,
                        })
                except Exception:
                    pass

        plt.show = _hooked_show  # type: ignore[assignment]
        try:
            from matplotlib.figure import Figure
            def _figure_show(self: Any, *_a: Any, **_kw: Any) -> None:
                _hooked_show()
            Figure.show = _figure_show  # type: ignore[assignment]
        except Exception:
            pass
    except ImportError:
        pass  # matplotlib 未安装,跳过

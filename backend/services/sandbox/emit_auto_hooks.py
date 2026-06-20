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


def _mimebundle_to_payload(data: dict) -> dict | None:
    """统一 mimebundle → emit payload 分发表(Phase 2c 配置中心)。

    优先级:富类型(图表) > 图片 > HTML 表格 > 通用文本。
    返回 None 时让调用方落回原 publish(打 stdout 等无害)。
    """
    # Plotly v1
    plotly_spec = data.get("application/vnd.plotly.v1+json")
    if plotly_spec:
        title = ""
        try:
            title = (plotly_spec.get("layout") or {}).get("title", {}).get("text", "")
        except Exception:
            pass
        return {
            "kind": "chart",
            "spec_format": "plotly",
            "title": title or "",
            "option": plotly_spec,
        }

    # Vega-Lite (altair) v3+
    for k, v in data.items():
        if k.startswith("application/vnd.vegalite"):
            return {
                "kind": "chart",
                "spec_format": "vegalite",
                "title": v.get("title", "") if isinstance(v, dict) else "",
                "option": v,
            }

    # 图片 (PIL.Image / matplotlib Figure 单独 display 等)
    # _repr_png_/_repr_jpeg_ 返回 base64 字符串或 bytes
    for mime in ("image/png", "image/jpeg", "image/svg+xml"):
        img = data.get(mime)
        if img:
            import base64
            if mime == "image/svg+xml":
                # SVG 是文本,不需要 base64
                svg_text = img if isinstance(img, str) else img.decode("utf-8", errors="ignore")
                return {
                    "kind": "image",
                    "mime_type": "image/svg+xml",
                    "svg": svg_text,
                    "name": "inline.svg",
                }
            # PNG/JPEG: 沙盒写到一个临时文件再走 emit_image (复用 OSS 上传链路)
            ext = "png" if mime == "image/png" else "jpg"
            try:
                # base64 字符串 → bytes
                if isinstance(img, str):
                    img_bytes = base64.b64decode(img)
                else:
                    img_bytes = img
            except Exception:
                continue
            return {
                "kind": "image",
                "mime_type": mime,
                "_inline_bytes": img_bytes,  # 让上层写出到 output_dir
                "_inline_ext": ext,
            }

    # text/html → 智能转 table (对标 Databricks display(df) / Hex Smart Cells)
    # df._repr_html_() 出 → pd.read_html 进,对称美。
    # 非表格 HTML(IPython.display.HTML('<div>'))不接通,return None。
    html = data.get("text/html")
    if html and isinstance(html, str):
        return _html_to_table_payload(html)

    # application/json / text/markdown 不接通(对标 Databricks/Hex 简化派):
    #   - JSON: LLM 可 print(json.dumps),无需独立 data block
    #   - Markdown: LLM 主消息本身就是 markdown,功能重复
    # 详见 docs/document/TECH_沙盒IO统一协议.md

    return None


# 表格预览行数上限 — 与 emit_protocol._TABLE_MAX_ROWS / 前端 MAX_PREVIEW_ROWS 对齐
_HTML_TABLE_MAX_ROWS = 200


def _html_to_table_payload(html: str) -> dict | None:
    """HTML(含 <table>)→ table payload。

    用 pandas.read_html 反向解析,对 df.to_html() / df._repr_html_() 天然对称。
    非表格 HTML / 空表格 / 解析失败 → return None(走静默丢弃,对齐 data/markdown)。

    清理:
      - 去掉 pandas 默认 index 反解产生的 "Unnamed: 0" 列
      - numpy.nan → None (JSON 序列化合法 + 前端 formatCell 能正确处理)
    """
    try:
        import pandas as pd
        from io import StringIO
    except ImportError:
        return None
    try:
        # StringIO 包装(pandas 2.x 起字符串字面量被弃用)
        dfs = pd.read_html(StringIO(html), flavor="lxml")
    except (ValueError, ImportError, Exception):
        return None
    if not dfs:
        return None
    df = dfs[0]
    if df.empty or len(df.columns) == 0:
        return None
    # 去掉 pandas df.to_html(默认 index=True)产生的 "Unnamed: 0" 索引列
    unnamed_cols = [c for c in df.columns if str(c).startswith("Unnamed:")]
    if unnamed_cols:
        df = df.drop(columns=unnamed_cols)
        if df.empty or len(df.columns) == 0:
            return None
    total_rows = len(df)
    truncated = total_rows > _HTML_TABLE_MAX_ROWS
    if truncated:
        df = df.head(_HTML_TABLE_MAX_ROWS)
    # numpy.nan → None (JSON 标准 NaN 非法,前端 JSON.parse 会崩)
    df = df.astype(object).where(df.notna(), None)
    return {
        "kind": "table",
        "columns": [str(c) for c in df.columns.tolist()],
        "rows": df.to_dict(orient="records"),
        "truncated": truncated,
    }


def install_ipython_display_shim(
    sandbox_globals: dict,
    emit_buffer: list[dict],
    output_dir: str = "",
) -> None:
    """Hook 真 IPython.display.publish_display_data,转发 mimebundle 到 emit。

    Phase 2 升级: 支持全 mimetype 集合(plotly/vegalite/png/jpeg/svg/html/json/markdown),
    对齐 Jupyter mimebundle 协议(行业事实标准)。
    详见 docs/document/TECH_沙盒产物协议.md

    注:真 IPython 包必须已 pip install(requirements.txt 已声明)。
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

    # 内联图片(_repr_png_/_repr_jpeg_)写到 output_dir 才能走 OSS 上传链路
    _inline_image_counter = {"n": 0}

    def _materialize_inline_image(payload: dict) -> dict | None:
        """把 _inline_bytes 字段的 image payload 写到 output_dir,转成普通 emit_image"""
        img_bytes = payload.pop("_inline_bytes", None)
        ext = payload.pop("_inline_ext", "png")
        if not img_bytes or not output_dir:
            return payload  # 无 output_dir 时退化(开发环境)
        try:
            os.makedirs(output_dir, exist_ok=True)
            _inline_image_counter["n"] += 1
            fname = f"display_{_inline_image_counter['n']}.{ext}"
            fpath = os.path.join(output_dir, fname)
            with open(fpath, "wb") as f:
                f.write(img_bytes)
            return {
                "kind": "image",
                "path": fpath,
                "name": fname,
                "alt": fname,
            }
        except Exception:
            return None

    def _hooked_publish(
        data: dict,
        metadata: dict | None = None,
        source: Any = None,
        *,
        transient: dict | None = None,
        update: bool = False,
        **kwargs: Any,
    ) -> None:
        """拦截 publish_display_data 用统一 mimetype 分发表转 emit_buffer"""
        if isinstance(data, dict):
            payload = _mimebundle_to_payload(data)
            if payload is not None:
                # 内联 image bytes → 写文件 → 走统一 OSS 链路
                if payload.get("kind") == "image" and "_inline_bytes" in payload:
                    materialized = _materialize_inline_image(payload)
                    if materialized:
                        emit_buffer.append(materialized)
                else:
                    emit_buffer.append(payload)
                return  # 不再调原始 publish(避免双重渲染)

        # 不是已知 mimetype,落回原 publish(打 stdout 等无害)
        try:
            _orig_publish(data, metadata, source, transient=transient, update=update, **kwargs)
        except Exception:
            pass

    _hooked_publish._emit_hooked = True  # type: ignore[attr-defined]
    ip_display.publish_display_data = _hooked_publish

    # display() 入口劫持:LLM 调 IPython.display.display(fig) 时直接走 buffer。
    # 不依赖 publish 链路 patch,IPython 内部 from import 会绑定原引用导致补丁失效。
    # Phase 2: 支持所有 _repr_*_ 而不仅 _repr_mimebundle_(pandas DataFrame 只有 _repr_html_)
    _REPR_TO_MIME = {
        "_repr_html_": "text/html",
        "_repr_png_": "image/png",
        "_repr_jpeg_": "image/jpeg",
        "_repr_svg_": "image/svg+xml",
        "_repr_json_": "application/json",
        "_repr_markdown_": "text/markdown",
        "_repr_latex_": "text/latex",
        "_repr_pdf_": "application/pdf",
    }

    def _hooked_display(*objs: Any, **kw: Any) -> None:
        for obj in objs:
            # 优先级 1: _repr_mimebundle_ (plotly v4+/altair 等富对象)
            bundle_fn = getattr(obj, "_repr_mimebundle_", None)
            if callable(bundle_fn):
                try:
                    result = bundle_fn()
                    bundle = result[0] if isinstance(result, tuple) and result else result
                    if isinstance(bundle, dict) and bundle:
                        _hooked_publish(bundle)
                        continue
                except Exception:
                    pass

            # 优先级 2: 单独 _repr_*_ 方法(pandas DataFrame _repr_html_ / PIL _repr_png_)
            bundle: dict = {}
            for repr_name, mime in _REPR_TO_MIME.items():
                fn = getattr(obj, repr_name, None)
                if callable(fn):
                    try:
                        val = fn()
                        if val is not None:
                            bundle[mime] = val
                    except Exception:
                        continue
            if bundle:
                _hooked_publish(bundle)

    _hooked_display._emit_hooked = True  # type: ignore[attr-defined]
    ip_display.display = _hooked_display

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
        from matplotlib import font_manager as fm
        from matplotlib._pylab_helpers import Gcf

        # 中文字体别名注册(对齐 mplfonts 第三方库, 行业最干净方案)
        #
        # Root cause: LLM 按 Windows/Mac 习惯写 rcParams['font.sans-serif']=['SimHei']
        # 等字体, Linux 服务器没装这些真名字体 → matplotlib fontManager 找不到 →
        # fallback DejaVu Sans → 中文方块。
        #
        # matplotlib 3.x 的 findfont 不走 fontconfig (OS 别名无效),
        # 必须给 fontManager.ttflist 直接 append 别名 FontEntry。
        # 让 LLM 写 SimHei/Microsoft YaHei/PingFang SC 等任意字体名,
        # matplotlib 都直接命中已装的 WenQuanYi 字体文件。LLM 0 感知。
        #
        # 调研: mplfonts 库源码 / 调研报告 / Matplotlib issue #19508 (closed not-planned)
        _WQY_FONT_PATH = "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc"
        _CHINESE_ALIASES = [
            "SimHei",              # Windows 黑体
            "Microsoft YaHei",     # Windows 雅黑
            "PingFang SC",         # macOS 苹方
            "Heiti SC",            # macOS 黑体简
            "STHeiti",             # macOS STHeiti
            "SimSun",              # Windows 宋体(LLM 偶尔写)
            "Noto Sans CJK SC",    # E2B / 行业字体名
        ]

        if (
            os.path.exists(_WQY_FONT_PATH)
            and not getattr(fm.fontManager, "_emit_chinese_aliases", False)
        ):
            for _alias in _CHINESE_ALIASES:
                fm.fontManager.ttflist.append(fm.FontEntry(
                    fname=_WQY_FONT_PATH,
                    name=_alias,
                    style="normal",
                    variant="normal",
                    weight="normal",
                    stretch="normal",
                    size="scalable",
                ))
            fm.fontManager._emit_chinese_aliases = True  # type: ignore[attr-defined]

        # 默认 rcParams(LLM 不动时也能渲染中文)
        plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False  # 防止负号显示为方块

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

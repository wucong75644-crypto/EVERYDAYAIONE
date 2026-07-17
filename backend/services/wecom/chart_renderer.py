"""把统一 ChartPart 渲染为企业微信可投递的 PNG。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

from playwright.async_api import Error as PlaywrightError, async_playwright


_ECHARTS_JS = (
    Path(__file__).parents[2]
    / "chart_runtime/node_modules/echarts/dist/echarts.min.js"
)
_MAX_OPTION_BYTES = 1_000_000
_RENDER_TIMEOUT_MS = 15_000


async def _abort_network(route: Any) -> None:
    await route.abort()


class ChartRenderError(RuntimeError):
    """图表无法安全渲染。"""


class WecomChartRenderer:
    """使用无网络 Chromium 将 ECharts option 渲染为固定尺寸 PNG。"""

    async def render(self, chart: Mapping[str, Any]) -> bytes:
        if chart.get("spec_format", "echarts") != "echarts":
            raise ChartRenderError("WECOM_CHART_FORMAT_UNSUPPORTED")
        option = chart.get("option")
        if not isinstance(option, Mapping) or not option:
            raise ChartRenderError("WECOM_CHART_OPTION_INVALID")
        encoded = json.dumps(option, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_OPTION_BYTES:
            raise ChartRenderError("WECOM_CHART_OPTION_TOO_LARGE")
        if not _ECHARTS_JS.is_file():
            raise ChartRenderError("WECOM_CHART_RUNTIME_MISSING")
        try:
            return await asyncio.wait_for(
                self._render_page(encoded),
                timeout=_RENDER_TIMEOUT_MS / 1000,
            )
        except asyncio.TimeoutError as error:
            raise ChartRenderError("WECOM_CHART_RENDER_TIMEOUT") from error
        except PlaywrightError as error:
            raise ChartRenderError("WECOM_CHART_RENDER_FAILED") from error

    async def _render_page(self, encoded: str) -> bytes:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": 1200, "height": 720},
                    device_scale_factor=1,
                )
                await context.route("**/*", _abort_network)
                page = await context.new_page()
                await page.set_content(
                    "<style>html,body{margin:0;background:#fff}"
                    "#chart{width:1200px;height:720px}</style>"
                    '<div id="chart"></div>'
                )
                await page.add_script_tag(path=str(_ECHARTS_JS))
                await page.evaluate(
                    """encoded => {
                        const option = JSON.parse(encoded);
                        option.animation = false;
                        const chart = echarts.init(
                            document.getElementById('chart'), null,
                            {renderer: 'canvas'}
                        );
                        chart.setOption(option, {notMerge: true});
                    }""",
                    encoded,
                )
                return await page.locator("#chart").screenshot(type="png")
            finally:
                await browser.close()

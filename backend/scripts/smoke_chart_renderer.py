"""部署门禁：验证真实 Chromium 能把 ECharts 渲染为 PNG。"""

import asyncio

from services.wecom.chart_renderer import WecomChartRenderer


async def main() -> None:
    image = await WecomChartRenderer().render({
        "spec_format": "echarts",
        "option": {
            "xAxis": {"type": "category", "data": ["A", "B"]},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "data": [1, 2]}],
        },
    })
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("CHART_RENDER_SMOKE_INVALID")
    print(f"chart_render_smoke=ok bytes={len(image)}")


if __name__ == "__main__":
    asyncio.run(main())

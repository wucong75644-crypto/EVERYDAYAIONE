import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from services.wecom.chart_renderer import ChartRenderError, WecomChartRenderer


@pytest.mark.asyncio
async def test_renderer_outputs_png_for_echarts_bar():
    renderer = WecomChartRenderer()
    with patch.object(
        renderer, "_render_page", new=AsyncMock(return_value=b"\x89PNG\r\n\x1a\n"),
    ):
        rendered = await renderer.render({
            "spec_format": "echarts",
            "option": {
                "xAxis": {"type": "category", "data": ["1月", "2月"]},
                "yAxis": {"type": "value"},
                "series": [{"type": "bar", "data": [120, 180]}],
            },
        })

    assert rendered.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_renderer_rejects_unsupported_format():
    with pytest.raises(ChartRenderError, match="FORMAT_UNSUPPORTED"):
        await WecomChartRenderer().render({
            "spec_format": "plotly",
            "option": {"data": []},
        })


@pytest.mark.asyncio
async def test_renderer_rejects_empty_option():
    with pytest.raises(ChartRenderError, match="OPTION_INVALID"):
        await WecomChartRenderer().render({"option": {}})


@pytest.mark.asyncio
async def test_renderer_wraps_timeout():
    renderer = WecomChartRenderer()
    with patch.object(
        renderer, "_render_page", new=AsyncMock(side_effect=asyncio.TimeoutError),
    ):
        with pytest.raises(ChartRenderError, match="RENDER_TIMEOUT"):
            await renderer.render({
                "option": {"series": [{"type": "bar", "data": [1]}]},
            })


@pytest.mark.asyncio
async def test_render_page_uses_isolated_browser_pipeline():
    screenshot = b"\x89PNG\r\n\x1a\n"
    locator = AsyncMock()
    locator.screenshot = AsyncMock(return_value=screenshot)
    page = AsyncMock()
    page.locator = lambda _selector: locator
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    chromium = AsyncMock()
    chromium.launch = AsyncMock(return_value=browser)
    playwright = AsyncMock()
    playwright.chromium = chromium
    manager = AsyncMock()
    manager.__aenter__ = AsyncMock(return_value=playwright)
    manager.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "services.wecom.chart_renderer.async_playwright",
        return_value=manager,
    ):
        rendered = await WecomChartRenderer()._render_page('{"series":[]}')

    assert rendered == screenshot
    context.route.assert_awaited_once()
    page.add_script_tag.assert_awaited_once()
    page.evaluate.assert_awaited_once()
    browser.close.assert_awaited_once()

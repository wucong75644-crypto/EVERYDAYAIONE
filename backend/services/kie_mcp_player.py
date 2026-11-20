"""Read-only MCP Apps player; no account credentials or paid actions in the view."""
from pathlib import Path
from urllib.parse import urlsplit
from mcp.server.fastmcp import FastMCP
from mcp import types

PLAYER_URI = 'ui://kie-video/player-v1.html'
MEDIA_ORIGIN = 'https://cdn.everydayai.com.cn'


def playable_urls(urls):
    result = []
    for value in urls:
        if not isinstance(value, str):
            continue
        try:
            parsed = urlsplit(value)
            if (parsed.scheme == 'https' and parsed.netloc == 'cdn.everydayai.com.cn'
                    and parsed.path.startswith('/personal/') and parsed.path.endswith('.mp4')
                    and not parsed.query and not parsed.fragment):
                result.append(value)
        except ValueError:
            continue
    return result


class PlayerMCP(FastMCP):
    """Small SDK 1.12 adapter for resource metadata, absent in FastMCP decorators."""

    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            if tool.name == 'show_video':
                tool.meta = {'ui': {'resourceUri': PLAYER_URI},
                             'openai/ui': {'preferredModelDisplayMode': 'inline'}}
        return tools

    async def list_resources(self):
        resources = await super().list_resources()
        for resource in resources:
            if str(resource.uri) == PLAYER_URI:
                resource.mimeType = "text/html;profile=mcp-app"
        return resources

    def _setup_handlers(self):
        super()._setup_handlers()
        original = self._mcp_server.request_handlers[types.ReadResourceRequest]

        async def read(request):
            if str(request.params.uri) != PLAYER_URI:
                return await original(request)
            return types.ServerResult(types.ReadResourceResult(contents=[types.TextResourceContents(
                uri=PLAYER_URI, mimeType='text/html;profile=mcp-app',
                text=Path(__file__).with_name('kie_mcp_player.html').read_text(),
                _meta={'ui': {'prefersBorder': True, 'csp': {
                    'resourceDomains': [MEDIA_ORIGIN], 'connectDomains': []}}},
            )]))
        self._mcp_server.request_handlers[types.ReadResourceRequest] = read

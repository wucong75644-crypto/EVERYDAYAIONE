from unittest.mock import AsyncMock
import pytest
from mcp import types
from services import kie_video_mcp as service
from services.kie_mcp_player import PLAYER_URI, playable_urls

URL = 'https://cdn.everydayai.com.cn/personal/test/video.mp4'

@pytest.mark.asyncio
async def test_player_protocol_and_readonly_result(monkeypatch):
    query = AsyncMock(return_value={'task_id':'known', 'state':'success', 'video_urls':[URL], 'storage_state':'stored'})
    monkeypatch.setattr(service, 'get_video_task', query)
    tool = next(t for t in await service.mcp.list_tools() if t.name == 'show_video')
    assert tool.meta['ui']['resourceUri'] == PLAYER_URI
    assert tool.annotations.readOnlyHint is True
    result = await service.show_video('known')
    assert result['video_urls'] == [URL]
    query.assert_awaited_once_with('known')
    handler = service.mcp._mcp_server.request_handlers[types.ReadResourceRequest]
    response = await handler(types.ReadResourceRequest(method='resources/read', params=types.ReadResourceRequestParams(uri=PLAYER_URI)))
    resource = response.root.contents[0]
    assert resource.mimeType == 'text/html;profile=mcp-app'
    assert resource.meta['ui']['csp']['resourceDomains'] == ['https://cdn.everydayai.com.cn']
    assert '<video ' in resource.text
    assert 'ui/notifications/tool-result' in resource.text
    assert 'vendor-secret' not in resource.text


def test_player_rejects_external_or_credential_urls():
    assert playable_urls([URL, 'javascript:alert(1)', 'https://cdn.everydayai.com.cn.evil/personal/x.mp4',
                          'https://user@cdn.everydayai.com.cn/personal/x.mp4',
                          URL+'?token=private', 'https://cdn.everydayai.com.cn/other/x.mp4']) == [URL]

import httpx
import pytest
from tests.fixtures.diagnostic_network_guard import block_diagnostic_network,DiagnosticNetworkBlocked


def test_real_semantic_query_cannot_silently_make_unapproved_embedding(monkeypatch):
    from core.config import get_settings
    from services.knowledge_workspace.semantic import query_embedding
    settings=get_settings().model_copy(update={'knowledge_semantic_enabled':True,'dashscope_api_key':'synthetic-never-network'})
    monkeypatch.setattr('services.knowledge_workspace.semantic.get_settings',lambda:settings)
    with block_diagnostic_network() as attempts:
        with pytest.raises(DiagnosticNetworkBlocked):query_embedding('退货')
    assert len(attempts)==1


@pytest.mark.asyncio
async def test_async_provider_is_blocked_before_transport():
    with block_diagnostic_network() as attempts:
        async with httpx.AsyncClient() as client:
            with pytest.raises(DiagnosticNetworkBlocked):await client.post('https://example.invalid/chat/completions',json={})
    assert len(attempts)==1

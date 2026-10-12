"""Continuation reconstructs complete loaded text without leaking or skipping fields."""
import pytest

from services.agent.agent_reach.contracts import ReachRequest, ReachResult, ReachItem, ReachError
from services.agent.agent_reach.pagination import paginate
from services.agent.agent_reach.xhs_adapter import XHSAdapter
from services.agent.agent_reach.service import resolve

URL = 'https://www.xiaohongshu.com/explore/abc?xsec_token=test'


def test_long_unicode_content_can_be_reconstructed():
    content = '字幕内容\n' * 5000
    original = ReachResult('bilibili', 'test', [ReachItem('字幕', 'https://www.bilibili.com/video/BV1234567890', content, 'transcript')])
    request = ReachRequest(task='读取', platform='bilibili', action='transcript', url=original.items[0].url)
    chunks = []
    for _ in range(20):
        page = paginate(original, request)
        chunks.extend(item.content for item in page.items)
        response = page.to_agent_result()
        assert not any(s['truncated'] for s in response.metadata['sources'])
        if not page.pagination['has_more_text']:
            assert page.status == 'success'
            break
        assert page.status == 'partial'
        request = request.model_copy(update={'cursor': page.pagination['next_cursor']})
    assert ''.join(chunks) == content
    assert original.items[0].content == content


def test_cursor_rejects_content_or_target_changes_and_invalid_position():
    request = ReachRequest(task='读取', platform='xiaohongshu', action='read', url=URL)
    result = ReachResult('xiaohongshu', 'test', [ReachItem('正文', URL, 'a'*9000)])
    cursor = paginate(result, request).pagination['next_cursor']
    request = request.model_copy(update={'cursor': cursor, 'url': URL + '&other=1'})
    with pytest.raises(ReachError, match='变化'):
        paginate(result, request)
    request = request.model_copy(update={'url': URL, 'cursor': cursor.rsplit(':', 1)[0] + ':999999'})
    with pytest.raises(ReachError, match='位置'):
        paginate(result, request)


def test_multiple_comments_continue_without_skipping():
    request = ReachRequest(task='评论', platform='xiaohongshu', action='read_comments', url=URL)
    result = ReachResult('xiaohongshu', 'test', [ReachItem(str(i), URL + '#'+str(i), str(i)*3500, 'comment') for i in range(3)])
    output = ''
    while True:
        page = paginate(result, request)
        output += ''.join(item.content for item in page.items)
        if not page.pagination['next_cursor']:
            break
        request = request.model_copy(update={'cursor': page.pagination['next_cursor']})
    assert output == ''.join(item.content for item in result.items)


async def adapter_with_detail(detail):
    adapter = XHSAdapter.__new__(XHSAdapter)
    calls = []
    async def identity():
        pass
    async def call(method, path, body):
        calls.append(body)
        return {'feed_id': 'abc', 'data': detail}
    adapter.identity, adapter.call = identity, call
    return adapter, calls


@pytest.mark.asyncio
async def test_xhs_body_excludes_comment_noise():
    adapter, calls = await adapter_with_detail({'note': {'title':'标题', 'desc':'正文'*5000, 'user':{'nickname':'作者'}},
                                               'comments': {'list': [{'content':'comment noise'}]}})
    result = await adapter.read(ReachRequest(task='读', platform='xiaohongshu', action='read', url=URL))
    assert result.items[0].content.endswith('作者：作者\n' + '正文'*5000)
    assert '"likes": null' in result.items[0].content
    assert '"comments_count": null' in result.items[0].content
    assert 'comment noise' not in result.items[0].content
    assert calls[0]['load_all_comments'] is False


@pytest.mark.asyncio
async def test_xhs_comments_respect_limit_and_page():
    adapter, calls = await adapter_with_detail({'note':{}, 'comments':{
        'list': [{'id':str(i),'content':str(i),'subComments':[{'content':'reply'}]} for i in range(8)], 'hasMore':True}})
    request = ReachRequest(task='评论', platform='xiaohongshu', action='read_comments', url=URL, max_results=3, page=2)
    result = await adapter.read(request)
    assert len(result.items) == 3
    assert [i.url.split('#')[-1] for i in result.items] == ['comment-3','comment-4','comment-5']
    assert result.pagination['next_page'] == 3
    assert calls[0]['comment_config']['max_comment_items'] == 7
    assert calls[0]['load_all_comments'] is True
    assert 'reply' in result.items[0].content


@pytest.mark.asyncio
async def test_xhs_cannot_reach_requested_page_is_not_empty_success():
    adapter, _ = await adapter_with_detail({'note':{}, 'comments':{'list':[], 'hasMore':True}})
    with pytest.raises(ReachError, match='未能加载'):
        await adapter.read(ReachRequest(task='评论', platform='xiaohongshu', action='read_comments', url=URL, page=2))


def test_search_cannot_silently_ignore_page():
    with pytest.raises(ValueError):
        ReachRequest(task='查询', platform='bilibili', action='search', query='test', page=2)
    with pytest.raises(ReachError):
        resolve(ReachRequest(task='正文', platform='xiaohongshu', action='read', url=URL, page=2))


@pytest.mark.asyncio
async def test_bili_comment_pages_preserve_boundary_and_do_not_write(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from services.agent.agent_reach.bili_bridge import execute
    calls = []
    async def identity(credential):
        return {'mid':42}
    async def info():
        return {'aid':123}
    async def comments(**kwargs):
        calls.append(kwargs['page_index'])
        start = (kwargs['page_index']-1)*20
        rows = [{'rpid':i,'content':{'message':str(i)},'member':{'uname':'author'}} for i in range(start,min(start+20,39))]
        return {'replies':rows,'page':{'count':39, 'size':20}}
    api = ModuleType('bilibili_api')
    api.Credential = lambda **kwargs: object()
    api.video = SimpleNamespace(Video=lambda **kwargs: SimpleNamespace(get_info=info))
    api.comment = SimpleNamespace(get_comments=comments, CommentResourceType=SimpleNamespace(VIDEO=1), OrderType=SimpleNamespace(TIME=0))
    network = ModuleType('bilibili_api.utils.network')
    network.request_settings = SimpleNamespace(set_wbi_retry_times=lambda n: None)
    client = ModuleType('bili_cli.client')
    client.get_self_info = identity
    monkeypatch.setitem(sys.modules, 'bilibili_api', api)
    monkeypatch.setitem(sys.modules, 'bilibili_api.utils.network', network)
    monkeypatch.setitem(sys.modules, 'bili_cli.client', client)
    ids = []
    page = 1
    while True:
        response = await execute({'secret':{},'account_id':'42','bvid':'BV1234567890',
                                  'request':{'action':'read_comments','page':page,'max_results':3}})
        assert len(response['comments']) <= 3
        ids.extend(int(row['id']) for row in response['comments'])
        if not response['has_more']:
            break
        page += 1
    assert ids == list(range(39))
    assert calls == [1]*7 + [2]*7


@pytest.mark.asyncio
async def test_execute_exposes_continuation_in_tool_summary(monkeypatch):
    from types import SimpleNamespace
    from services.agent.agent_reach.service import execute
    from services.agent.agent_reach import social_adapters
    async def query(request, runner, connection):
        return ReachResult('bilibili','test',[ReachItem('字幕','https://www.bilibili.com/video/BV1234567890','a'*8000,'transcript')])
    monkeypatch.setattr(social_adapters, 'query_social', query)
    settings = SimpleNamespace(agent_reach_platforms='bilibili', agent_reach_bin_dir='/tmp')
    request = ReachRequest(task='字幕',platform='bilibili',action='transcript',url='https://www.bilibili.com/video/BV1234567890')
    first = await execute(request,settings,timeout=1)
    cursor = first.metadata['pagination']['next_cursor']
    assert cursor in first.summary
    assert first.status == 'partial'
    second = await execute(request.model_copy(update={'cursor':cursor}),settings,timeout=1)
    assert second.metadata['pagination']['has_more_text'] is False
    assert second.status == 'success'

@pytest.mark.asyncio
async def test_xhs_details_preserve_real_metrics_and_units():
    adapter, _ = await adapter_with_detail({'note': {'noteId': 'abc', 'desc': '正文',
        'interactInfo': {'likedCount': '1.2万', 'commentCount': '291', 'collectedCount': '0'}}})
    result = await adapter.read(ReachRequest(task='读', platform='xiaohongshu', action='read', url=URL))
    text = result.items[0].content
    assert '"note_id": "abc"' in text
    assert '"likes": "1.2万"' in text
    assert '"comments_count": "291"' in text
    assert '"collected_count": "0"' in text
    assert '"shared_count": null' in text

@pytest.mark.asyncio
async def test_xhs_search_result_link_can_be_passed_unchanged_to_details():
    adapter, calls = await adapter_with_detail({'note': {'desc': '真实正文'}})
    original_call = adapter.call
    async def call(method, path, body):
        if path == 'feeds/search':
            return {'feeds': [{'id': 'abc', 'xsecToken': 'read-token', 'noteCard': {'displayTitle': '标题'}}]}
        return await original_call(method, path, body)
    adapter.call = call
    search = await adapter.read(ReachRequest(task='搜', platform='xiaohongshu', action='search', query='文具'))
    assert 'note_id：abc' in search.items[0].content
    assert 'xsec_token' in search.warnings[0]
    await adapter.read(ReachRequest(task='读', platform='xiaohongshu', action='read', url=search.items[0].url))
    assert calls[0]['feed_id'] == 'abc'
    assert calls[0]['xsec_token'] == 'read-token'
    assert calls[0]['load_all_comments'] is False

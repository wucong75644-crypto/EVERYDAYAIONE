"""Contract, routing and isolation regressions without live accounts."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from services.agent.agent_reach.contracts import ReachError, ReachItem, ReachRequest, ReachResult, validate_url
from services.agent.agent_reach.service import resolve
from services.agent.agent_reach.social_adapters import query_social
from services.agent.agent_reach.runner import ToolRunner
from services.agent.agent_reach.network import public_address


@pytest.mark.parametrize('url', ['file:///tmp/x', 'https://user:secret@example.com', 'https://example.com:8080', 'https://example.com/\n'])
def test_unsafe_url(url):
    with pytest.raises(ReachError):
        validate_url(url)


@pytest.mark.parametrize('extra', [{'shell':'echo x'}, {'org_id':'forged'}, {'max_results':11}, {'action':'set_like','platform':'twitter'}])
def test_untrusted_input_rejected(extra):
    with pytest.raises((ValidationError, ReachError)):
        ReachRequest.model_validate({'task':'test', **extra})


def test_automatic_route_and_mismatch():
    request = resolve(ReachRequest(task='总结', url='https://www.youtube.com/watch?v=abcdefghijk'))
    assert (request.platform, request.action) == ('youtube', 'transcript')
    with pytest.raises(ReachError):
        resolve(ReachRequest(task='test', platform='github', url='https://x.com/i/status/123'))


def test_source_bounds_and_no_metadata_as_fulltext():
    result = ReachResult('github', 'test', [ReachItem('repo', 'https://github.com/a/b', 'a'*10000, 'metadata')]).to_agent_result()
    assert result.status == 'partial'
    assert result.metadata['sources'][0]['content_kind'] == 'metadata'
    assert result.metadata['sources'][0]['truncated']
    assert len(result.summary) < 6500
    assert 'https://github.com/a/b' in result.summary


@pytest.mark.asyncio
async def test_dns_mixed_public_private_rejected(monkeypatch):
    async def addresses(*args, **kwargs):
        return [(2,1,6,'',('8.8.8.8',443)), (2,1,6,'',('127.0.0.1',443))]
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', addresses)
    with pytest.raises(ReachError, match='内网'):
        await public_address('https://example.com')


class Runner:
    def __init__(self, payload):
        self.payload, self.args = payload, None
    async def run(self, name, args, **kwargs):
        self.args = args
        return self.payload


@pytest.mark.asyncio
async def test_bili_missing_subtitle_is_failure():
    runner = Runner({'ok':True,'data':{'subtitle':{'available':False,'text':'','items':[]}}})
    request = ReachRequest(task='总结', platform='bilibili', action='transcript', url='https://www.bilibili.com/video/BV1234567890')
    with pytest.raises(ReachError) as error:
        await query_social(request, runner, None)
    assert error.value.code == 'NO_TRANSCRIPT'
    assert '--subtitle' in runner.args


@pytest.mark.asyncio
async def test_reddit_structured_post_not_raw_listing():
    runner = Runner({'post':{'title':'title','selftext':'body','permalink':'/r/test/comments/abc/title/'}, 'comments':[]})
    request = ReachRequest(task='读取', platform='reddit', action='read', url='https://www.reddit.com/r/test/comments/abc/title/')
    result = await query_social(request, runner, {'secret':{'cookies':{'reddit_session':'dummy'}}})
    assert result.items[0].content == 'body'
    assert runner.args[-1] == 'abc'


@pytest.mark.asyncio
async def test_runner_isolates_home_environment_and_limits_output(tmp_path):
    executable = tmp_path / 'twitter'
    executable.write_text('#!/usr/bin/python3\nimport os,json\nprint(json.dumps({"home":os.environ["HOME"],"secret":os.environ.get("APP_SECRET")}))\n')
    executable.chmod(0o700)
    os.environ['APP_SECRET'] = 'must-not-leak'
    try:
        data = await ToolRunner(str(tmp_path)).run('twitter', [])
        assert data['secret'] is None
        assert not Path(data['home']).exists()
        executable.write_text('#!/usr/bin/python3\nprint("a"*2000001)\n')
        with pytest.raises(ReachError) as error:
            await ToolRunner(str(tmp_path)).run('twitter', [])
        assert error.value.code == 'OUTPUT_LIMIT'
    finally:
        os.environ.pop('APP_SECRET', None)


@pytest.mark.asyncio
async def test_runner_cancellation_cleans_home(tmp_path):
    marker = tmp_path / 'marker'
    executable = tmp_path / 'twitter'
    executable.write_text('#!/usr/bin/python3\nimport os,time\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').write_text(os.environ["HOME"])\ntime.sleep(60)\n')
    executable.chmod(0o700)
    task = asyncio.create_task(ToolRunner(str(tmp_path)).run('twitter', []))
    for _ in range(100):
        if marker.exists():
            break
        await asyncio.sleep(.01)
    assert marker.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not Path(marker.read_text()).exists()


@pytest.mark.parametrize('action,outcome', [('read','allow'), ('set_like','allow'), ('create_comment','require_confirmation'), ('create_post','require_confirmation')])
def test_new_tool_policy(action, outcome):
    from services.tools import ToolContext, ToolPolicy, build_legacy_catalog
    context = ToolContext(actor_user_id='actor', workspace_owner_id='actor', org_id='org',
        context_scope='user', personal_context_allowed=True, agent_domain='general',
        permission_mode='ask', execution_mode='interactive', conversation_id='conversation',
        call_id='call', confirmation_available=True, feature_flags={'agent_reach_enabled':True})
    args = {'task':'test', 'platform':'twitter','action':action,'url':'https://x.com/i/status/123',
            'content':'body', 'connection_id':'00000000-0000-0000-0000-000000000001'}
    decision = ToolPolicy(build_legacy_catalog()).decide('agent_reach', context, args)
    assert decision.outcome == outcome


def test_new_tool_disabled_and_plan_denied():
    from dataclasses import replace
    from services.tools import ToolContext, ToolPolicy, build_legacy_catalog
    context = ToolContext(actor_user_id='actor', workspace_owner_id='actor', org_id='org',
        context_scope='user', personal_context_allowed=True, agent_domain='general',
        permission_mode='ask', execution_mode='interactive', feature_flags={})
    policy = ToolPolicy(build_legacy_catalog())
    assert policy.decide('agent_reach', context, {'task':'test'}).outcome == 'deny'
    assert policy.decide('agent_reach', replace(context, permission_mode='plan', feature_flags={'agent_reach_enabled':True}), {'task':'test'}).outcome == 'deny'


@pytest.mark.asyncio
async def test_xhs_identity_mismatch_prevents_write():
    from services.agent.agent_reach.xhs_adapter import XHSAdapter
    adapter = XHSAdapter('{"connection":"http://127.0.0.1:18060"}',
        {'id':'connection','account_id':'expected','secret':{'service_token':'dummy'}})
    calls = []
    async def call(method, path, body=None):
        calls.append(path)
        return {'is_logged_in':True,'user_id':'other'}
    adapter.call = call
    request = ReachRequest(task='like', platform='xiaohongshu', action='set_like',
        connection_id='00000000-0000-0000-0000-000000000001',
        url='https://www.xiaohongshu.com/explore/abc?xsec_token=dummy')
    with pytest.raises(ReachError) as error:
        await adapter.write(request)
    assert error.value.code == 'ACCOUNT_MISMATCH'
    assert calls == ['login/status']


@pytest.mark.asyncio
async def test_youtube_comment_requires_matching_channel_and_returns_id():
    import json
    from services.agent.agent_reach.write_adapters import write
    class HTTP:
        async def get(self, url, **kwargs):
            return b'{"items":[{"id":"channel"}]}'
        async def request(self, method, url, **kwargs):
            assert kwargs['json']['snippet']['channelId'] == 'channel'
            assert kwargs['json']['snippet']['topLevelComment']['snippet']['textOriginal'] == 'comment'
            return b'{"id":"remote-comment"}'
    request = ReachRequest(task='comment', platform='youtube', action='create_comment',content='comment',
        connection_id='00000000-0000-0000-0000-000000000001',url='https://www.youtube.com/watch?v=abcdefghijk')
    result = await write(request, {'account_id':'channel','secret':{'access_token':'dummy'}},None,HTTP(),None)
    assert result.receipt['remote_id'] == 'remote-comment'


@pytest.mark.asyncio
async def test_cross_process_account_lock_is_released_on_cancel():
    import sys
    from uuid import uuid4
    from services.agent.agent_reach.locks import account_lock
    identity = 'test-' + str(uuid4())
    backend = str(Path(__file__).resolve().parents[1])
    code = ('import sys,asyncio;sys.path.insert(0,' + repr(backend) + ')\n'
        'from services.agent.agent_reach.locks import account_lock\n'
        'async def main():\n'
        ' async with account_lock(' + repr(identity) + '):\n'
        '  print("locked",flush=True)\n'
        '  await asyncio.to_thread(sys.stdin.readline)\n'
        'asyncio.run(main())\n')
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
    try:
        assert await asyncio.wait_for(process.stdout.readline(),3) == b'locked\n'
        async def acquire():
            async with account_lock(identity):
                return True
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(acquire(), .1)
        process.stdin.write(b'release\n')
        await process.stdin.drain()
        await asyncio.wait_for(process.wait(),3)
        assert await asyncio.wait_for(acquire(),1)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_youtube_comments_key_never_in_url_and_empty_is_empty():
    from services.agent.agent_reach.public_adapters import youtube_comments
    class HTTP:
        async def get(self, url, **kwargs):
            assert 'private-key' not in url
            assert kwargs['headers']['X-Goog-Api-Key'] == 'private-key'
            return b'{"items":[]}'
    request = ReachRequest(task='comments', platform='youtube', action='read_comments', url='https://youtu.be/abcdefghijk')
    result = await youtube_comments(request, HTTP(), 'private-key')
    assert result.to_agent_result().status == 'empty'

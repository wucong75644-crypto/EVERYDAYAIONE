"""Media requests must survive ToolCall's immutable JSON snapshot."""
import pytest

from services.tools import ToolCall, ToolContext, ToolPolicy, build_legacy_catalog
from services.tools.spec import thaw


@pytest.mark.parametrize('action,role', [('create_post', 'image'), ('upload_video', 'video')])
@pytest.mark.parametrize('invalid', [False, True])
def test_frozen_media_requests_keep_validation_and_confirmation(action, role, invalid):
    args = dict(task='private publish test', platform='xiaohongshu', action=action,
                connection_id='00000000-0000-0000-0000-000000000001',
                title='测试', content='测试正文', visibility='private', tags=[],
                media=[dict(name='fixture.png', resource_ref='fref1_fixture', role=role)])
    if invalid:
        args['media'][0]['unexpected'] = 'must still be rejected'
    call = ToolCall('call', 'agent_reach', args)
    context = ToolContext(actor_user_id='actor', workspace_owner_id='actor', org_id='org',
        context_scope='user', personal_context_allowed=True, agent_domain='general',
        permission_mode='ask', execution_mode='interactive', conversation_id='conversation',
        call_id='call', confirmation_available=True, feature_flags={'agent_reach_enabled': True})
    policy = ToolPolicy(build_legacy_catalog())
    plain = policy.decide(call.name, context, args)
    frozen = policy.plan_batches([call], context)[0][0].decision
    assert frozen.outcome == plain.outcome
    assert frozen.reason == plain.reason
    assert frozen.outcome == ('deny' if invalid else 'require_confirmation')
    assert thaw(call.arguments) == args

import copy
from uuid import uuid4
import pytest
from services.agent.image.reference_roles import canonical_role, is_product
from services.adapters.dashscope.responses import response_input, chat_messages


@pytest.mark.parametrize('role', ['product','商品图','detail','product_detail','product/商品图',
                                 'detail/商品细节','产品摄影参考图',' PRODUCT '])
def test_product_roles_share_the_same_semantics(role):
    assert canonical_role(role) == 'product' and is_product(role)


@pytest.mark.parametrize('role', ['style_reference','风格参考图','商品风格参考（未核实）','wrong',None])
def test_unknown_and_style_references_never_become_product_evidence(role):
    assert not is_product(role)


@pytest.mark.parametrize('width', [1,2,10,100])
def test_real_actor_id_is_bounded_paired_and_stable_across_protocols(width):
    key=f'actor-call:{uuid4()}:round:{width}:index:{width}:'+'a'*24
    messages=[{'role':'assistant','content':None,'tool_calls':[{'id':key,'type':'function',
        'function':{'name':'activate_skill','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':key,'content':'{"ok":false}'}]
    original=copy.deepcopy(messages)
    response=response_input(messages,'qwen3.8-max')
    chat=chat_messages(messages,cache=None)
    wire=response[0]['call_id']
    assert 1 <= len(wire) <= 64
    assert response[1]['call_id']==chat[0]['tool_calls'][0]['id']==chat[1]['tool_call_id']==wire
    assert response_input(messages,'qwen3.8-max')==response
    assert messages==original


def test_same_prefix_is_not_truncated_and_provider_replay_is_not_rewritten():
    from services.adapters.dashscope.call_identity import wire_call_id
    prefix='x'*200
    assert wire_call_id(prefix+'a') != wire_call_id(prefix+'b')
    replay={'model':'qwen3.8-max','items':[{'type':'reasoning','id':'r'*100,'summary':[]}]}
    assert response_input([{'role':'assistant','_dashscope_output':replay}], 'qwen3.8-max')[0]['id']=='r'*100

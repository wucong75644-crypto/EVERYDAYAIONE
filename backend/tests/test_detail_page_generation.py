from types import SimpleNamespace
import pytest
from core.exceptions import AppException
from services.agent.image.ecommerce_planner.page_profile import capabilities,profile
from services.adapters.chat_protocol import chat_messages
from services.adapters.factory import get_all_models,MODEL_REGISTRY
from schemas.detail_project import DetailProjectSettingsPatch


def settings(**overrides):
    return SimpleNamespace(kie_api_key='test',dashscope_api_key='test',detail_page_planning_seconds=1200,
        **{f'detail_{provider}_{direction}_credits_per_million':overrides.get(f'{provider}_{direction}',1)
          for provider in ('kimi','gemini') for direction in ('input','output')})


def test_page_model_policy_has_no_implicit_price_or_model_fallback():
    missing=settings(kimi_input=None)
    assert capabilities(missing)[0]['available'] is False
    with pytest.raises(AppException,match='费率'):
        profile(missing,'kimi-k3')
    with pytest.raises(AppException):profile(settings(),'gpt-5-6-luna')
    assert profile(settings(),'kimi-k3').ecom_image_planning_model=='kimi-k3'
    assert profile(settings(),'kimi-k3').ecom_image_planning_reasoning=='high'
    assert profile(settings(),'gemini-3.8-flash').ecom_image_planning_reasoning=='medium'
    assert not {'kimi-k3','gemini-3.8-flash'} & get_all_models().keys()
    assert {'kimi-k3','gemini-3.8-flash'} <= MODEL_REGISTRY.keys()


def test_chat_protocol_preserves_multimodal_order_and_raw_text_without_mutation():
    raw=[{'role':'developer','content':'规则'},{'role':'user','content':[{'type':'input_text','text':'  发财\n  '},
        {'type':'input_image','image_url':'https://example.invalid/1'},
        {'type':'input_text','text':'参考图片2'},{'type':'input_image','image_url':'https://example.invalid/2'}]}]
    result=chat_messages(raw)
    assert result[0]['role']=='system' and raw[0]['role']=='developer'
    assert result[1]['content'][0]['text']=='  发财\n  '
    assert [part['image_url']['url'] for part in result[1]['content'] if part['type']=='image_url']==['https://example.invalid/1','https://example.invalid/2']


def test_page_schema_allows_default_14_and_single_15_but_upload_limit_stays_separate():
    assert DetailProjectSettingsPatch(version=1,content_type='default',image_count=14,prompt_model='kimi-k3').image_count==14
    assert DetailProjectSettingsPatch(version=1,content_type='detail_page',image_count=15).image_count==15
    with pytest.raises(ValueError):DetailProjectSettingsPatch(version=1,image_count=16)


def test_default_entry_freezes_two_groups_and_raw_requirements(monkeypatch):
    from unittest.mock import MagicMock
    from services import detail_page_generation as page
    configured=settings()
    configured.detail_page_generation_enabled=True
    configured.chat_image_max_requests=15
    configured.chat_image_max_credits=300
    monkeypatch.setattr(page,'get_settings',lambda:configured)
    monkeypatch.setattr(page,'chat_image_acceptance_allowed',lambda *_:True)
    monkeypatch.setattr(page,'validate_single_image_request',lambda *_:{'estimated_credits':1})
    raw='  要有发财的感觉\n不要改变商品  '
    project={'id':'project-1','version':3,'prompt_model':'kimi-k3','content_type':'default',
        'image_count':14,'images':[],'requirement':raw,'platform':'taobao','language':'zh-CN','aspect_ratio':'1:1','quality':'1k'}
    refs=[{'file_id':'fid_a','source_id':'image-a','role':'product','workspace_path':'a.png'},
          {'file_id':'fid_b','source_id':'image-b','role':'product','workspace_path':'b.png'}]
    monkeypatch.setattr(page,'PageImageInputResolver',lambda *_:SimpleNamespace(bind=lambda _:refs))
    generation=object.__new__(page.DetailPageGeneration)
    generation.db=MagicMock()
    generation.user_id='user-1';generation.org_id=None
    generation.projects=SimpleNamespace(get_by_id=lambda _:project,get_ai_input_project=lambda _:project)
    generation.read=lambda _:project
    generation.start('project-1',3,'request-1')
    rows=generation.db.rpc.call_args.args[1]['p_plans']
    assert [row['input_snapshot']['task_type'] for row in rows]==['main_images','detail_page']
    assert [row['image_count'] for row in rows]==[7,7]
    for row in rows:
        assert row['input_snapshot']['messages'][0]['parts'][0]['text']==raw
        assert row['input_snapshot']['resolved_references']==refs
        assert row['model_settings']['profile']['ecom_image_planning_model']=='kimi-k3'


def test_database_guards_have_actionable_errors_without_sql():
    from services.detail_page_generation import page_error
    error=page_error(RuntimeError('DETAIL_PROJECT_VERSION_CONFLICT; private SQL'))
    assert error.code=='DETAIL_PROJECT_VERSION_CONFLICT'
    assert 'private' not in str(error)
    assert page_error(RuntimeError('arbitrary private SQL')).code=='DETAIL_GENERATION_FAILED'


async def test_accepted_group_is_not_reverified_or_resubmitted(monkeypatch):
    from unittest.mock import MagicMock
    from services import detail_page_generation as page
    generation=object.__new__(page.DetailPageGeneration)
    generation.db=MagicMock();generation.user_id='user-1'
    generation.db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data=[{
        'request_params':{'_media_request_v1':{'origin':{'plan_source':{'item_id':'item-1'}}}}}]
    resolver=MagicMock(side_effect=AssertionError('accepted inputs should not be resolved again'))
    monkeypatch.setattr(page,'PageImageInputResolver',resolver)
    assert await generation.accept({'id':'plan-1','items':[{'item_id':'item-1'}]})=={'outcome':'replay'}
    resolver.assert_not_called()
    generation.db.rpc.assert_not_called()


def test_acceptance_diagnostics_excludes_private_database_details():
    from services.detail_page_generation import acceptance_diagnostics
    error=RuntimeError('failed row contains private prompt and https://token')
    error.sqlstate='23502'
    error.diag=SimpleNamespace(table_name='tasks',column_name='conversation_id',constraint_name=None,
        message_detail='private prompt',context='private SQL')
    assert acceptance_diagnostics(error)=={'sqlstate':'23502','table_name':'tasks','column_name':'conversation_id'}
    error.diag.column_name='https://private-token'
    assert 'column_name' not in acceptance_diagnostics(error)

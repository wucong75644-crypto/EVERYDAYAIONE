from services.message_utils import format_message
import pytest


@pytest.mark.parametrize('started', [False, True])
def test_rest_exposes_native_visibility_without_changing_content_or_error(started):
    marker={'origin':'internal_continuation','output_started':started}
    raw=dict(id='message',conversation_id='conversation',role='assistant',created_at='now',content='[]',
        status='pending',generation_params={'delivery_presentation':marker})
    rendered=format_message(raw)
    assert rendered['delivery_presentation']==marker and rendered['content']==[]
    assert rendered['generation_params']==raw['generation_params']
    assert format_message({**raw,'status':'failed','is_error':True})['is_error']


@pytest.mark.parametrize('marker', [None, {'origin':'ordinary','output_started':False},
    {'origin':'internal_continuation','output_started':'false'}])
def test_invalid_or_missing_marker_cannot_hide_normal_messages(marker):
    rendered=format_message(dict(id='message',conversation_id='conversation',role='assistant',created_at='now',
        content=[{'type':'text','text':'真实回复'}],generation_params={'delivery_presentation':marker}))
    assert rendered['delivery_presentation'] is None
    assert rendered['content']==[{'type':'text','text':'真实回复'}]

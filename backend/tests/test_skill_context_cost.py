from tests.fixtures.skill_context_cost import request_context_cost


def test_breakdown_counts_calls_images_and_repeated_methods_without_leaking_content():
    secret='private-user-sentence'
    body={'model':'synthetic','messages':[{'role':'system','content':'complete-method\ncomplete-method'},
        {'role':'user','content':[{'type':'text','text':secret},{'type':'image_url','image_url':{'url':'data:private-image'}}]},
        {'role':'assistant','tool_calls':[{'id':'one','function':{'name':'save_task_text','arguments':'{"text":"full-plan"}'}}]}],
        'tools':[{'type':'function','function':{'name':'save_task_text'}}]}
    result=request_context_cost(body,{'method':'complete-method'})
    assert result['total_method_occurrences']=={'method':2}
    assert result['image_count']==1 and result['message_tool_call_bytes']>0 and result['tool_schema_bytes']>0
    assert secret not in repr(result) and 'private-image' not in repr(result) and 'full-plan' not in repr(result)

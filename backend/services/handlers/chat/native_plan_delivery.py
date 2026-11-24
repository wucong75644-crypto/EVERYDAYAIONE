"""Replayable text projection of an already persisted native ready plan."""
from copy import deepcopy
import hashlib

from services.task_execution.content_delivery import MAX_BYTES,input_revision

KEY='_native_plan_text_delivery_v1'


def stage(runtime,request,results):
    if runtime is None or len(results)!=1:
        return
    call,result,is_error,_=results[0]
    if call['name']!='plan_ecommerce_images' or is_error:
        return
    from .content_tools import image_writing_only_request,requires_continued_delivery
    metadata=getattr(result,'metadata',{})
    text=metadata.get('server_plan_text')
    if (metadata.get('status')!='ready' or not metadata.get('plan_id')
            or metadata.get('server_plan_owner_task_id')!=str(request.task_id)
            or not isinstance(text,str) or not text or len(text.encode())>MAX_BYTES
            or not image_writing_only_request(request,runtime)
            or requires_continued_delivery(request,runtime)
            or any(not getattr(item,'delivered',False) for item in
                   getattr(getattr(runtime,'task_user_changes',None),'items',()))
            or getattr(runtime,'_steer_messages',())):
        return
    runtime.checkpoint_base[KEY]={'version':1,'phase':'pending','owner_task_id':str(request.task_id),
        'input_revision':input_revision(runtime,request),'plan_id':metadata['plan_id'],
        'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}


async def deliver(runtime,request,blocks,sink,totals):
    if runtime is None:
        return False
    value=runtime.checkpoint_base.get(KEY)
    if not isinstance(value,dict) or value.get('version')!=1 or value.get('phase') not in {'pending','committed'}:
        return False
    if value.get('owner_task_id')!=str(request.task_id) or value.get('input_revision')!=input_revision(runtime,request):
        runtime.checkpoint_base[KEY]={**value,'phase':'superseded'}
        return False
    text=value.get('text')
    if not isinstance(text,str) or hashlib.sha256(text.encode()).hexdigest()!=value.get('sha256'):
        raise ValueError('PLANNER_DELIVERY_CONTENT_CHANGED')
    if not any(block.get('content_delivery_final') and block.get('text')==text for block in blocks):
        block={'type':'text','text':text,'content_delivery_final':True}
        blocks.append(block);totals.text+=text
        await sink.on_block(block)
    runtime.checkpoint_base[KEY]={**deepcopy(value),'phase':'committed'}
    return True

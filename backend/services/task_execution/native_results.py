"""Import actual read results; never manufacture a business-write receipt.

New managed tasks journal supported reads through the existing fenced Actor
ledger. Reach writes use their explicit operation adapter; old tasks retain
their existing lifecycle.
"""
import hashlib
from uuid import UUID

from .contracts import Artifact,TaskError,TaskState
from .reducer import digest,ready_steps
from services.handlers.chat.tool_lifecycle import ActorToolLifecycle

PLAIN_READS=frozenset({'file_search','file_analyze','web_search','search_knowledge','get_conversation_context','erp_api_search',
    'local_data','local_product_stats','local_stock_query','local_product_identify','local_platform_map_query',
    'local_compare_stats','local_shop_list','local_warehouse_list','local_supplier_list',
    'knowledge_outline','knowledge_search','knowledge_read','knowledge_follow_reference','knowledge_discover'})
ACTION_READ_RULES=frozenset({'erp_query','erp_raw_read','scheduled_task','agent_reach'})
VALIDATOR='native.read.result'
EMPTY_VALIDATOR='native.read.empty'
MCP_VALIDATOR='native.mcp.read'


def reviewed_mcp_read(spec):
    from services.tools.mcp_audit import connector_facts
    return bool(spec is not None and spec.executor_type=='mcp' and spec.effects==('none',)
        and spec.replay_requirement=='record_required' and connector_facts(spec) is not None)


def verify_mcp_audit(spec,audit):
    from services.tools.mcp_audit import connector_facts
    expected=connector_facts(spec)
    if expected is None or audit.get('tool_name')!=spec.name or any(audit.get(k)!=v for k,v in expected.items()):
        raise TaskError('TASK_NATIVE_MCP_IDENTITY_CHANGED')


def supports_read(spec,decision):
    return bool(spec is not None and decision.outcome=='allow' and decision.operation=='read'
        and (spec.name in PLAIN_READS or spec.policy_rules.action_rule in ACTION_READ_RULES or reviewed_mcp_read(spec)))


class ManagedReadLifecycle(ActorToolLifecycle):
    records_cache_consumption=True
    def __init__(self,handler,context,*,spec,registry=None,recovery_only=False):
        super().__init__(handler,context,recovery_only=recovery_only)
        self.spec=spec
        self.registry=registry
        self.requires_fresh_read=False

    def _enabled(self,decision):
        native=super()._enabled(decision)
        if native:return True
        if not supports_read(self.spec,decision):return False
        if (not getattr(self.handler,'_actor_enabled',False) or self.store is None
                or not getattr(self.handler,'_actor_turn_id',None) or not getattr(self.handler,'_actor_execution_token',None)):
            raise PermissionError('TASK_READ_ACTOR_REQUIRED')
        return True

    async def replay(self,call,context,decision):
        if self.spec.name=='manage_scheduled_task' and decision.operation in {'proposal','business_write'}:
            from .scheduled_operations import ScheduledOperationLifecycle
            self.write_lifecycle=ScheduledOperationLifecycle(self.handler,context,registry=self.registry,recovery_only=self.recovery_only)
            return await self.write_lifecycle.replay(call,context,decision)
        if self.spec.name=='agent_reach' and decision.operation=='business_write':
            from .reach_operations import ReachOperationLifecycle
            self.write_lifecycle=ReachOperationLifecycle(self.handler,context,registry=self.registry,recovery_only=self.recovery_only)
            return await self.write_lifecycle.replay(call,context,decision)
        if supports_read(self.spec,decision):
            remember_arguments(self.handler,call,context)
            self.requires_fresh_read=(self.spec.name=='erp_product_query' and call.arguments.get('action')=='product_detail'
                and any(op.tool_name=='erp_execute' and op.status in {'accepted','uncertain'}
                    for op in self.handler._actor_runtime.task_execution_state.operations))
        return await super().replay(call,context,decision)

    async def begin(self,call,context,decision):
        if getattr(self,'write_lifecycle',None) is not None:return await self.write_lifecycle.begin(call,context,decision)
        return await super().begin(call,context,decision)

    async def complete(self,result):
        if getattr(self,'write_lifecycle',None) is not None:return await self.write_lifecycle.complete(result)
        return await super().complete(result)

    async def consume_cached(self,call,context,decision,result):
        if not supports_read(self.spec,decision):return result
        existing=await self.begin(call,context,decision)
        if existing is not None:return existing
        # The record describes an authorized cache consumption. Preserve its
        # cached/handler_started flags and original provenance; do not pretend
        # that a second query or business handler ran.
        from services.handlers.chat_tool_mixin import ChatToolMixin
        await ChatToolMixin._complete_actor_tool_invocation(self.handler,store=self.store,task_id=context.task_id,
            turn_id=self.handler._actor_turn_id,tool_call_id=call.call_id,status='succeeded',result=result,error_message='')
        return result


def remember_arguments(handler,call,context):
    from services.tools.spec import thaw
    from services.tool_invocation_store import hash_tool_arguments
    import json
    runtime=handler._actor_runtime;state=runtime.task_execution_state
    if (context.task_id!=runtime.task_id or context.actor_user_id!=str(state.actor_user_id)
        or context.workspace_owner_id!=state.execution_identity.workspace_owner_id
        or context.org_id!=(str(state.org_id) if state.org_id else None)
        or context.conversation_id!=str(state.conversation_id)):raise TaskError('TASK_READ_ARGUMENT_SCOPE_DENIED')
    previous=getattr(runtime,'task_read_arguments',None)
    value=previous or {'version':1,'task_id':runtime.task_id,'turn_id':str(handler._actor_turn_id),'calls':{}}
    if (value['task_id'],value['turn_id'])!=(runtime.task_id,str(handler._actor_turn_id)):
        raise TaskError('TASK_READ_ARGUMENT_SCOPE_DENIED')
    args=thaw(call.arguments)
    item={'tool_name':call.name,'arguments':args,'args_hash':hash_tool_arguments(args)}
    if call.call_id in value['calls'] and value['calls'][call.call_id]!=item:raise TaskError('TASK_READ_ARGUMENTS_CHANGED')
    updated={**value,'calls':{**value['calls'],call.call_id:item}}
    if len(updated['calls'])>128 or len(json.dumps(updated,ensure_ascii=False).encode())>4*1024*1024:
        raise TaskError('TASK_READ_ARGUMENTS_LIMIT')
    runtime.task_read_arguments=updated


def restore_arguments(raw,*,task_id,turn_id):
    from services.tool_invocation_store import hash_tool_arguments
    if (not isinstance(raw,dict) or set(raw)!={'version','task_id','turn_id','calls'} or raw['version']!=1
        or raw['task_id']!=str(task_id) or raw['turn_id']!=str(turn_id) or not isinstance(raw['calls'],dict) or len(raw['calls'])>128):
        raise TaskError('TASK_READ_ARGUMENT_SCOPE_DENIED')
    for call_id,value in raw['calls'].items():
        if (not isinstance(call_id,str) or not 1<=len(call_id)<=200 or not isinstance(value,dict)
            or set(value)!={'tool_name','arguments','args_hash'} or not isinstance(value['arguments'],dict)
            or hash_tool_arguments(value['arguments'])!=value['args_hash']):raise TaskError('TASK_READ_ARGUMENTS_CHANGED')
    import copy
    return copy.deepcopy(raw)


def lifecycle_for(handler,context,registry,name,*,recovery_only=False):
    state=getattr(getattr(handler,'_actor_runtime',None),'task_execution_state',None)
    spec=registry.get(name)
    if isinstance(state,TaskState) and state.image_binding_version>=3 and name=='generate_video':
        from .video_operations import VideoLifecycle
        return VideoLifecycle(handler,context,registry=registry,recovery_only=recovery_only)
    if isinstance(state,TaskState) and state.image_binding_version>=3 and name=='file_delete':
        from .file_deletions import FileDeleteLifecycle
        return FileDeleteLifecycle(handler,context,registry=registry,recovery_only=recovery_only)
    if isinstance(state,TaskState) and state.image_binding_version>=3 and name=='restore_file':
        from .file_restores import FileRestoreLifecycle
        return FileRestoreLifecycle(handler,context,registry=registry,recovery_only=recovery_only)
    if isinstance(state,TaskState) and state.image_binding_version>=3 and name=='erp_execute':
        from .native_writes import NativeWriteLifecycle
        return NativeWriteLifecycle(handler,context,registry=registry,recovery_only=recovery_only)
    if isinstance(state,TaskState) and state.image_binding_version>=3 and name=='code_execute':
        from .sandbox_operations import SandboxOperationLifecycle
        return SandboxOperationLifecycle(handler,context,registry=registry,recovery_only=recovery_only)
    if (isinstance(state,TaskState) and state.image_binding_version>=3 and spec is not None
            and (spec.name in PLAIN_READS or spec.policy_rules.action_rule in ACTION_READ_RULES or reviewed_mcp_read(spec))):
        return ManagedReadLifecycle(handler,context,spec=spec,registry=registry,recovery_only=recovery_only)
    return ActorToolLifecycle(handler,context,recovery_only=recovery_only)


def import_result(state,value,record,*,owner_task_id,context,registry):
    """The ledger supplies result + args hash; arguments are only a hash witness."""
    from services.tool_invocation_store import hash_tool_arguments
    from services.tools import ToolCall
    from services.tools.result_payload import extension_of,restore_result
    from services.tools.runtime_context import check_result_resources
    from services.tools.spec import thaw
    step=next((s for s in ready_steps(state) if s.id==value.step_id),None)
    if step is None:raise TaskError('TASK_STEP_NOT_READY')
    if record.get('status')!='succeeded':raise TaskError('TASK_NATIVE_RESULT_NOT_CONFIRMED')
    spec=registry.get(record.get('tool_name'))
    if spec is None or step.capability!=spec.capability:raise TaskError('TASK_NATIVE_RESULT_CAPABILITY_MISMATCH')
    arguments=thaw(value.arguments) if value.arguments is not None else record.get('arguments')
    if not isinstance(arguments,dict) or hash_tool_arguments(arguments)!=record.get('args_hash'):
        raise TaskError('TASK_NATIVE_RESULT_ARGUMENTS_MISMATCH')
    call=ToolCall(record['tool_call_id'],spec.name,arguments)
    from dataclasses import replace
    scoped=replace(context,task_id=str(owner_task_id),call_id=call.call_id)
    from services.tools.policy import ToolPolicy
    decision=ToolPolicy(registry).decide(call.name,scoped,call.arguments)
    if not supports_read(spec,decision):raise TaskError('TASK_NATIVE_RESULT_READ_REQUIRED')
    payload=record.get('result')
    try:
        extension=extension_of(payload)
        if extension is None:raise ValueError()
        audit=extension['audit']
        if spec.executor_type=='mcp':verify_mcp_audit(spec,audit)
        if (audit.get('actor_user_id')!=str(state.actor_user_id) or audit.get('workspace_owner_id')!=state.execution_identity.workspace_owner_id
            or audit.get('org_id')!=(str(state.org_id) if state.org_id else None)
            or audit.get('args_hash')!=record['args_hash']):raise ValueError()
        check_result_resources(scoped,payload)
        result=restore_result(payload,call=call,context=scoped,decision=decision)
        if result.status not in {'success','empty'} or result.error is not None or result.exception is not None or result.execution.status!='succeeded' or result.execution.cancelled:
            raise ValueError()
    except (ValueError,TypeError,KeyError,PermissionError):raise TaskError('TASK_NATIVE_RESULT_UNVERIFIED') from None
    data=result.artifacts.data
    if value.kind=='file':
        from .native_files import verify_file
        checksum,extra=verify_file(state,extension,scoped)
        extra={**extra,'cached':result.execution.cached,'replayed':result.execution.replayed}
        text=None
    elif value.kind=='data':
        if data is None:raise TaskError('TASK_NATIVE_RESULT_DATA_UNAVAILABLE')
        columns=extension['raw'].get('columns')
        checksum=digest({'data':data,'columns':columns})
        extra={'data':data,'columns':columns,'cached':result.execution.cached,'replayed':result.execution.replayed}
        text=None
    else:
        text=result.model_content('tool_loop')
        if not isinstance(text,str) or not text or len(text)>50000:raise TaskError('TASK_NATIVE_RESULT_TEXT_UNAVAILABLE')
        checksum=hashlib.sha256(text.encode()).hexdigest()
        extra={'text':text,'cached':result.execution.cached,'replayed':result.execution.replayed}
    identity=digest([record['id'],value.step_id,value.kind,checksum])
    extra['result_status']=result.status
    validations=('artifact.exists',VALIDATOR)+((EMPTY_VALIDATOR,) if result.status=='empty' else ())
    if spec.executor_type=='mcp':validations+=(MCP_VALIDATOR,)
    artifact=Artifact(id='native-read-'+identity[:32],kind=value.kind,reference='task-native-result:'+record['id'],
        sha256=checksum,source_task_id=UUID(record['task_id']),delivery_task_id=UUID(str(owner_task_id)),
        source_call_id=record['tool_call_id'],step_id=step.id,validations=validations,inline_text=text)
    return artifact,extra


def read_result(state,artifact,record,*,context=None):
    from services.tools.result_payload import extension_of,decode_raw
    if VALIDATOR not in artifact.validations or artifact.reference!='task-native-result:'+record['id']:
        raise TaskError('TASK_NATIVE_RESULT_REFERENCE_INVALID')
    try:
        extension=extension_of(record['result']);raw=decode_raw(extension)
        execution=extension['execution'];audit=extension['audit']
        if MCP_VALIDATOR in artifact.validations:
            from services.tools import ToolCall
            from services.tools.catalog import build_capability_catalog
            from services.tools.policy import ToolPolicy
            registry=build_capability_catalog();spec=registry.get(record.get('tool_name'))
            verify_mcp_audit(spec,audit)
            if (context is None or context.actor_user_id!=str(state.actor_user_id)
                or context.org_id!=(str(state.org_id) if state.org_id else None)
                or context.workspace_owner_id!=state.execution_identity.workspace_owner_id
                or context.conversation_id!=str(state.conversation_id)):raise TaskError('TASK_NATIVE_MCP_CONTEXT_REQUIRED')
            args=record.get('arguments')
            from services.tool_invocation_store import hash_tool_arguments
            if not isinstance(args,dict) or hash_tool_arguments(args)!=record['args_hash']:raise TaskError('TASK_NATIVE_RESULT_ARGUMENTS_MISMATCH')
            call=ToolCall(record['tool_call_id'],spec.name,args)
            if not supports_read(spec,ToolPolicy(registry).decide(call.name,context,call.arguments)):
                raise TaskError('TASK_NATIVE_RESULT_READ_REQUIRED')
        expected_status='empty' if EMPTY_VALIDATOR in artifact.validations else 'success'
        if (record['status']!='succeeded' or extension['status']!=expected_status
            or execution['status']!='succeeded' or execution['cancelled'] or extension['error'] is not None
            or extension['kind']=='exception'
            or audit.get('actor_user_id')!=str(state.actor_user_id)
            or audit.get('workspace_owner_id')!=state.execution_identity.workspace_owner_id
            or audit.get('org_id')!=(str(state.org_id) if state.org_id else None)
            or audit.get('args_hash')!=record.get('args_hash')):raise ValueError()
        if extension['kind']=='agent' and raw.status!=expected_status:raise ValueError()
        if artifact.kind=='file':
            from .native_files import verify_file
            checksum,value=verify_file(state,extension,context)
            if checksum!=artifact.sha256:raise ValueError()
        elif artifact.kind=='data':
            value={'data':raw.data,'columns':extension['raw'].get('columns')}
            if digest(value)!=artifact.sha256:raise ValueError()
        elif artifact.kind=='text':
            value={'text':artifact.inline_text}
            # Recompute the same native projection used at import. Checking
            # only the saved inline copy would miss a changed native receipt.
            overrides=extension['model_overrides']
            if 'tool_loop' in overrides:text=overrides['tool_loop']
            elif extension['kind']=='agent':text=raw.to_tool_content()
            elif extension['kind']=='file_read':text=raw.text
            elif extension['kind']=='form':text=raw.llm_hint
            else:text=str(raw)
            if text!=artifact.inline_text or hashlib.sha256(text.encode()).hexdigest()!=artifact.sha256:raise ValueError()
        else:raise ValueError()
    except (ValueError,KeyError,TypeError,AttributeError):raise TaskError('TASK_NATIVE_RESULT_CHANGED') from None
    return {**value,'result_status':expected_status}


def fact_sources(native,state,expected=None):
    """Already imported data is evidence, never an instruction or new grant."""
    artifacts={a.id:a for a in state.artifacts if a.kind=='data' and VALIDATOR in a.validations}
    ids=list(artifacts) if expected is None else [f['artifact_id'] for f in expected]
    facts=[]
    import json
    for key in ids:
        artifact=artifacts.get(key)
        if artifact is None:raise TaskError('TASK_NATIVE_RESULT_REFERENCE_INVALID')
        record_id=artifact.reference.removeprefix('task-native-result:')
        row=native.owner.db.table('tool_invocations').select('id,task_id,conversation_id,tool_call_id,tool_name,args_hash,status,result').eq('id',record_id).single().execute().data
        if (not row or str(row['task_id'])!=str(artifact.source_task_id) or row['conversation_id']!=str(state.conversation_id)
                or row['tool_call_id']!=artifact.source_call_id):raise TaskError('TASK_NATIVE_RESULT_SCOPE_DENIED')
        parent=native.owner.db.table('tasks').select('id,user_id,org_id,conversation_id').eq('id',row['task_id']).single().execute().data
        if (not parent or parent['user_id']!=str(state.actor_user_id) or parent.get('org_id')!=(str(state.org_id) if state.org_id else None)
                or parent['conversation_id']!=str(state.conversation_id)):raise TaskError('TASK_NATIVE_RESULT_SCOPE_DENIED')
        value=read_result(state,artifact,row)
        body=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        facts.append({'id':'tool:'+artifact.id,'artifact_id':artifact.id,'sha256':artifact.sha256,'tool_name':row['tool_name'],'text':body})
    return facts

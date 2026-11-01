"""Host-owned postconditions, proved by later native read invocations."""
from uuid import UUID
from .contracts import TaskError

KEY='_erp_readback_v1'
FIELDS_KEY='_erp_readback_fields_v1'
# Exact stable scalar fields already mapped by product_add_update_simple and
# returned by item.single.get. No numeric/unit conversion, SKU, stock or pricing.
FIELD_LIMITS={'title':128,'barcode':256,'unit':128,'remark':8000,'shortTitle':128}
INPUT_FIELDS={'outer_id':'outerId','title':'title','barcode':'barcode','unit':'unit','remark':'remark','short_title':'shortTitle'}
METHOD='item.general.addorupdate'
READ_METHOD='item.single.get'


def source_fingerprint(client):
    """Opaque exact-session identity. Never store any credential values.

    A different session requires a persisted provider-observed refresh path.
    Unproved/manual switches stay unlinked. Existing token refresh stays intact.
    """
    import hashlib,json
    values=[getattr(client,key,None) for key in ('_app_key','_app_secret','_access_token','_base_url')]
    if any(not isinstance(value,str) or not value for value in values):return None
    return hashlib.sha256(json.dumps([getattr(client,'_org_id',None),*values],ensure_ascii=False).encode()).hexdigest()


def title_expectation(method,parameters):
    if not isinstance(parameters,dict):return None
    parameters={k:v for k,v in parameters.items() if k not in {'pageNo','pageSize'}}
    if method!=METHOD or set(parameters)!={'outerId','title'}:return None
    outer=parameters['outerId'];title=parameters['title']
    if not isinstance(outer,str) or not outer.strip() or len(outer)>64:return None
    if not isinstance(title,str) or not title.strip() or len(title)>128:return None
    return {'version':1,'kind':'product.title','outer_id':outer,'title':title}


def product_expectation(method,parameters):
    original=title_expectation(method,parameters)
    if original is not None:return original
    if method!=METHOD or not isinstance(parameters,dict):return None
    parameters={k:v for k,v in parameters.items() if k not in {'pageNo','pageSize'}}
    if not {'outerId','title'}<=parameters.keys() or not parameters.keys()<={'outerId',*FIELD_LIMITS}:return None
    outer=parameters['outerId'];values={k:v for k,v in parameters.items() if k!='outerId'}
    if not isinstance(outer,str) or not outer.strip() or len(outer)>64:return None
    if any(not isinstance(v,str) or len(v)>FIELD_LIMITS[k] for k,v in values.items()) or not values['title'].strip():return None
    return {'version':2,'kind':'product.fields','outer_id':outer,'values':values}


def original_expectation(method,arguments):
    if not isinstance(arguments,dict) or not set(arguments)<={'params','category','action'}:return None
    values=arguments.get('params')
    if not isinstance(values,dict) or not set(values)<=set(INPUT_FIELDS):return None
    return product_expectation(method,{INPUT_FIELDS[k]:v for k,v in values.items()})


def projection_key(expected):
    return FIELDS_KEY if expected.get('kind')=='product.fields' else KEY


def project_fields(method,parameters,data):
    base=project_detail(method,parameters,data)
    if base is None:return None
    values={k:v for k,v in data.items() if k in FIELD_LIMITS and isinstance(v,str) and len(v)<=FIELD_LIMITS[k]}
    return {**base,'item':{'outerId':base['item']['outerId'],**values}}


def view_matches(expected,view):
    if not isinstance(view,dict):return False
    if expected['kind']=='product.title':
        item={'outerId':expected['outer_id'],'title':expected['title']}
    else:
        item=view.get('item')
        if (not isinstance(item,dict) or not set(item)<={'outerId',*FIELD_LIMITS} or item.get('outerId')!=expected['outer_id']
            or any(not isinstance(item.get(k),str) or item[k]!=v for k,v in expected['values'].items())):return False
    return view=={'version':1,'method':READ_METHOD,'source_fingerprint':view.get('source_fingerprint'),
        'parameters':{'outerId':expected['outer_id']},'item':item}


def project_detail(method,parameters,data):
    if not isinstance(parameters,dict):return None
    parameters={k:v for k,v in parameters.items() if k not in {'pageNo','pageSize'}}
    if method!=READ_METHOD or set(parameters)!={'outerId'}:return None
    if not isinstance(data,dict) or data.get('success') is not True:return None
    from .native_writes import receipt_needs_follow_up
    if receipt_needs_follow_up(data):return None
    outer=data.get('outerId');title=data.get('title')
    if not isinstance(outer,str) or not isinstance(title,str) or len(outer)>64 or len(title)>128:return None
    if outer!=parameters['outerId']:return None
    return {'version':1,'method':READ_METHOD,'parameters':{'outerId':outer},'item':{'outerId':outer,'title':title}}


def expectation(proof):
    value=(proof.get('dispatch') or {}).get('postcondition')
    if value is None:return None
    if not isinstance(value,dict):raise TaskError('TASK_ERP_POSTCONDITION_UNCONFIRMED')
    if value.get('kind')=='product.fields':
        fields=value.get('values')
        parameters={'outerId':value.get('outer_id'),**(fields if isinstance(fields,dict) else {})}
        shape=product_expectation(METHOD,parameters)
    else:
        shape=title_expectation(METHOD,{'outerId':value.get('outer_id'),'title':value.get('title')})
    if (proof.get('domain')!='erp' or proof.get('intent')!={'category':'product','action':'product_add_update_simple'}
        or proof['dispatch'].get('method')!=METHOD
        or {k:v for k,v in value.items() if k!='source_fingerprint'}!=shape
        or not isinstance(value.get('source_fingerprint'),str) or len(value['source_fingerprint'])!=64
        or any(c not in '0123456789abcdef' for c in value['source_fingerprint'])):
        raise TaskError('TASK_ERP_POSTCONDITION_UNCONFIRMED')
    return value


def same_verified_source(proof):
    expected=expectation(proof)
    if expected is None:return False
    try:
        from datetime import datetime
        read=proof['readback'];view=read['result']['tool_result']['raw']['metadata'][projection_key(expected)]
        current=view['source_fingerprint'];start=expected['source_fingerprint']
        if current==start:return True
        path=proof.get('source_rotations')
        if not isinstance(path,list) or not 1<=len(path)<=32 or proof.get('org_id') is None:return False
        before=datetime.fromisoformat(read['completed_at'].replace('Z','+00:00'))
        seen={start};prior=None
        for edge in path:
            observed=datetime.fromisoformat(edge['observed_at'].replace('Z','+00:00'))
            target=edge['new']
            if (edge['org_id']!=proof['org_id'] or edge['old']!=start or target in seen
                or not isinstance(target,str) or len(target)!=64 or any(c not in '0123456789abcdef' for c in target)
                or observed>before or (prior is not None and observed<prior)):return False
            start=target;seen.add(start);prior=observed
        return start==current
    except (TypeError,KeyError,ValueError,AttributeError):return False


def verified_readback(state,proof):
    """Prove the observed target state, never the termination of an unknown write.

    Callers settling effects must independently require the original acknowledged
    method protocol. A timed-out provider request can still execute later.
    """
    expected=expectation(proof);read=proof.get('readback')
    if expected is None or read is None:return False
    from .native_writes import receipt_needs_follow_up
    if receipt_needs_follow_up((proof.get('response') or {}).get('body',{})):return False
    try:
        UUID(read['id']);payload=read['result']['tool_result'];audit=payload['audit'];execution=payload['execution']
        from datetime import datetime
        started=datetime.fromisoformat(read['started_at'].replace('Z','+00:00'))
        completed=datetime.fromisoformat(read['completed_at'].replace('Z','+00:00'))
        cutoff_key='write_invocation_completed_at' if proof.get('phase')=='dispatched' else 'write_returned_at'
        returned=datetime.fromisoformat(read[cutoff_key].replace('Z','+00:00'))
        if proof.get('phase')=='dispatched' and (read.get('write_phase')!='dispatched' or proof.get('response') is not None):return False
        if started<=returned or completed<started:return False
        view=payload['raw']['metadata'][projection_key(expected)]
        if not same_verified_source(proof):return False
        if (read['tool_name']!='erp_product_query' or read['status']!='succeeded' or payload['kind']!='agent'
            or payload['status']!='success' or payload.get('error') is not None or execution['status']!='succeeded'
            or execution['handler_started'] is not True or execution['cancelled'] is not False
            or execution['cached'] is not False or execution['replayed'] is not False
            or audit['tool_name']!=read['tool_name'] or audit['tool_call_id']!=read['tool_call_id']
            or audit['task_id']!=read['task_id'] or audit['args_hash']!=read['args_hash']
            or audit['actor_user_id']!=str(state.actor_user_id) or audit['org_id']!=proof['org_id']
            or audit['workspace_owner_id']!=state.execution_identity.workspace_owner_id
            or audit['conversation_id']!=str(state.conversation_id)
            or not view_matches(expected,view)):return False
    except (KeyError,TypeError,ValueError):return False
    return True


def read_instruction(proof,*,target_state_observed=False):
    value=expectation(proof)
    if value is None:return None
    from .native_writes import receipt_needs_follow_up
    if receipt_needs_follow_up((proof.get('response') or {}).get('body',{})):
        return {'state':'requires_method_specific_verification',
            'instruction':'原接口包含异步、部分受理或失败信息，普通商品状态查询不能证明整个原请求结束。'
            '保留原实例标识和逐项回执，等待该方法可关联本次操作的终态证据；不重复提交或轮询全局进度。'}
    if proof.get('phase')=='dispatched' and target_state_observed:
        return {'state':'await_provider_terminal_evidence',
            'instruction':'真实查询已观察到目标商品字段，但原写请求是否已结束仍未知，可能迟到执行。保留uncertain及原记录，'
            '不能据此完成原写效果、重发或继续新的写操作；停止重复读取相同状态，等待可关联原请求的终态证据。'}
    read=proof.get('readback')
    if read is not None:
        try:observed=read['result']['tool_result']['raw']['metadata'][projection_key(value)]
        except (TypeError,KeyError):observed={}
        if not isinstance(observed,dict) or not same_verified_source(proof):
            return {'state':'requires_connection_verification',
                'instruction':'ERP连接或session已经变化，当前回执不能关联原写入。保留原受理记录并说明待核实原因；'
                '停止重复查询或写入，待连接身份核实后再继续，不能自动重发。'}
    return {'state':'query_if_needed','tool_name':'erp_product_query','arguments':{'action':'product_detail','params':{'outer_id':value['outer_id']}},
        'instruction':'先按当前权限执行一次真实商品详情查询，再read_task_write_result核实原写操作。'
        '只以原调用结束之后同商品、全部指定字段一致的原生查询回执核实目标状态；丢确认时不能声称收到了写入成功回执。不要再次提交写操作。'}

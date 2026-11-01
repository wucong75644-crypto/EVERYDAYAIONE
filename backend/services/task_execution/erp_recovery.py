"""Read-only recovery facts from the native ERP journal, never resend authority."""
import json
from .contracts import TaskError
from .reducer import digest

SECRET_KEYS={'password','defaultpassword','secret','appsecret','token','accesstoken','refreshtoken',
    'authorization','cookie','appkey','sign','signature'}
MAX_BYTES=131072


def request_evidence(method,parameters):
    """Preserve the actually mapped request, including batch targets, without auth.

    Evidence is for inspection, never reconstructing a retry or proving success.
    Large/opaque values remain explicitly incomplete; original hashes survive.
    """
    redacted=[];visited=0;incomplete=False
    def walk(value,path,depth=0):
        nonlocal visited,incomplete
        visited+=1
        if visited>4096 or depth>32:
            incomplete=True;return {'omitted':True}
        if isinstance(value,dict):
            result={}
            for key,item in value.items():
                if not isinstance(key,str):raise TaskError('TASK_ERP_REQUEST_EVIDENCE_INVALID')
                location=path+[key]
                if ''.join(c for c in key.lower() if c.isalnum()) in SECRET_KEYS:
                    redacted.append(location);result[key]={'redacted':True}
                else:result[key]=walk(item,location,depth+1)
            return result
        if isinstance(value,(list,tuple)):return [walk(item,path+[i],depth+1) for i,item in enumerate(value[:4096])]
        if isinstance(value,str) and value.lstrip().startswith(('{','[')):
            try:decoded=json.loads(value)
            except (ValueError,RecursionError):return value
            if isinstance(decoded,(dict,list)):return {'json_value':walk(decoded,path+['json_value'],depth+1)}
        return value
    projected=walk(parameters,[])
    # Do not change the original write for an overlarge recovery projection.
    if len(json.dumps(projected,ensure_ascii=False,allow_nan=False).encode())>MAX_BYTES:
        projected=None;redacted=[];incomplete=True
    value={'version':1,'method':method,'parameters_sha256':digest(parameters),'parameters':projected,
        'redacted_paths':redacted,'incomplete':incomplete}
    return {**value,'sha256':digest(value)}


def recovery_facts(proof,observed,*,state=None):
    dispatch=proof.get('dispatch') or {};saved=dispatch.get('request_evidence')
    if saved is not None:
        if (not isinstance(saved,dict) or saved.get('version')!=1 or saved.get('method')!=dispatch.get('method')
            or saved.get('parameters_sha256')!=dispatch.get('parameters_sha256')
            or saved.get('sha256')!=digest({k:v for k,v in saved.items() if k!='sha256'})):
            raise TaskError('TASK_ERP_REQUEST_EVIDENCE_UNVERIFIED')
    body=(proof.get('response') or {}).get('body')
    pending=[([],body)];handles=[];items=[];visited=0;incomplete=False
    while pending:
        path,value=pending.pop();visited+=1
        if visited>2048:incomplete=True;break
        if isinstance(value,dict):
            for key in ('asyncTaskId','jobId','taskId'):
                identifier=value.get(key)
                if isinstance(identifier,str) and identifier.strip() and len(identifier)<=200:
                    handles.append({'path':path+[key],'value':identifier})
                elif type(identifier) is int and identifier>0:
                    handles.append({'path':path+[key],'value':identifier})
            if path and isinstance(path[-1],int) and len(items)<100:
                identity={k:v for k,v in value.items() if k in {'id','outerId','skuOuterId','tid','sid','itemId'}
                    and (isinstance(v,str) and len(v)<=200 or type(v) is int)}
                flag=value.get('success',value.get('isSuccess'))
                status='reported_success' if flag is True else 'reported_failure' if flag is False else 'unverified'
                items.append({'path':path,'identity':identity,'response_status':status})
            for key,item in reversed(list(value.items())):
                if isinstance(item,(dict,list)):pending.append((path+[key],item))
        elif isinstance(value,list):
            if len(value)>2048-visited:incomplete=True
            pending.extend((path+[index],item) for index,item in reversed(list(enumerate(value[:2048-visited]))))
    if len(items)>=100 or len(handles)>100:incomplete=True
    from .native_writes import receipt_needs_follow_up
    needs_follow_up=observed!='completed' or receipt_needs_follow_up(body)
    from .erp_postconditions import read_instruction,verified_readback
    target_state_observed=bool(state is not None and verified_readback(state,proof))
    instruction=read_instruction(proof,target_state_observed=target_state_observed)
    if observed=='completed':
        action='complete_from_verified_receipt'
    elif instruction is not None:action=instruction['state']
    else:action='requires_method_specific_verification'
    from .erp_contracts import contract_for
    return {'version':1,'can_resend':False,'method':dispatch.get('method'),'native_phase':proof.get('phase'),
        'verification_contract':contract_for(dispatch.get('method')),
        'request_evidence':saved,'provider_handles':handles[:100],'item_observations':items,'observations_incomplete':incomplete,
        'needs_follow_up':needs_follow_up,'next_action':action,'target_state_observed':target_state_observed,
        'effect_terminal':observed=='completed',
        'completion_basis':'postcondition_observed' if observed=='completed' and proof.get('readback') is not None else
            'native_receipt' if observed=='completed' else None,
        'instruction':'这是原请求及原接口观察，不授权重发。逐项success不证明整批完成；taskId等只供核实，'
            '没有本次实例终态协议时停止轮询全局进度，说明待核实原因并保留任务和已有结果。'}

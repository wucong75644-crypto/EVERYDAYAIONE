"""Authenticated logical-task controls; model tools do not expose this API."""
from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind


def scope_database(db,user_id,org_id):
    return ScopedDatabaseClient(getattr(db,'_db',db),DatabaseScope(user_id,org_id,DatabaseAccessKind.RUNTIME))


def control(db,task,*,user_id,org_id,action):
    root=(task.get('request_params') or {}).get('_agent_task_root')
    if not root: return None
    database=scope_database(db,user_id,org_id)
    for attempt in range(3):
        result=database.rpc('control_agent_task',{
            'p_root_task_id':root,'p_user_id':user_id,'p_org_id':org_id,'p_action':action}).execute()
        data=result.data if result else None
        if not isinstance(data,dict): raise RuntimeError('TASK_CONTROL_RESULT_INVALID')
        if data.get('outcome')!='retry': break
    if data.get('outcome')=='delegate':
        current=data.get('task')
        if (not isinstance(current,dict) or current.get('user_id')!=user_id or current.get('org_id')!=org_id
                or current.get('conversation_id')!=task.get('conversation_id')):
            raise RuntimeError('TASK_CONTROL_SCOPE_DENIED')
        # Existing Actor functions remain responsible for snapshots and fences.
        from services.conversation_task import control_actor_task,resume_actor_task
        outcome = (resume_actor_task(database,current,user_id,org_id,_logical_delegate=True) if action=='resume' else
            control_actor_task(database,current,user_id,org_id,action,_logical_delegate=True))
        return {**outcome,'task_id':current['id'],'task_slot_id':(current.get('request_params') or {}).get('_task_slot_id'),
            **{key:current.get(key) for key in (
            'client_task_id','external_task_id','assistant_message_id','conversation_id')}}
    if data.get('outcome')=='retry': raise RuntimeError('TASK_CONTROL_OWNER_CHANGED_RETRY')
    if action!='cancel' and data.get('outcome')=='already_cancelled':
        return {**data,'outcome':'terminal','status':'cancelled'}
    return data

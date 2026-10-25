"""Per-snapshot evidence progress. A miss narrows advice, never authorization or recall."""
from psycopg.types.json import Jsonb

QUERY_INSTRUCTIONS = '''具体问题不是整库审阅：目录和检索摘录用于定位，只读最相关的1–3项并按需补条件、例外；证据足够就回答，不为凑齐全框架未读数量继续读。找不到时先在当前授权根内扩大范围，根范围仍无命中可换一组核心词再查一次；不要把完整问句不断拆成更多关键词。search_guidance要求停止或没有新增证据时，停止当前缺失问题的检索，不再通过outline、逐个read或重复分页穷举；其他尚未处理的问题或授权根仍可正常查询。缺资料通常用1–2句话说明当前可访问范围没有找到，并只给一个必要的补充建议；不复述旧对话里的业务数值，不输出工具名、状态码、查询清单、目录遍历、内部预算或长篇免责声明。部分命中就简要回答有依据的部分并给来源，再指出缺少的部分。只有用户明确要求整库审阅、全面汇总或全部来源时才遍历完整框架。'''


def feedback(state, root, scope, root_wide, args, result):
    """Only compare evidence searches in the same effective scope; pagination remains usable."""
    if args.get('mode','evidence')!='evidence':
        if result['items']:state['last_search_empty_stop']=False
        return result
    progress=state.setdefault('search_progress',{})
    key=root+':'+(root if root_wide else scope or root)
    entry=progress.setdefault(key,{'seen':[],'empty':0})
    handles={item['handle'] for item in result['items']}
    seen=set(entry['seen']);novel=handles-seen
    entry['seen']=sorted(seen|handles)
    if handles:
        entry['empty']=0
    elif not args.get('cursor'):
        entry['empty']+=1
    stop=None
    if not result['items'] and args.get('cursor'):
        state['last_search_empty_stop']=False
        return {**result,'stop_reason':'no_new_evidence','search_guidance':'这一页没有新增内容，不代表整个范围没有资料。按已定位的来源回答，确有新问题可另行检索。'}
    if not result['items']:
        if not root_wide:
            advice='当前目录没有命中。可扩大到上一级或当前授权根；不要读无关节点。'
        elif entry['empty']<2:
            advice='当前授权根没有命中。可换一组核心关键词再查一次；若仍无命中就简短说明缺资料。'
        else:
            stop='no_relevant_evidence'
            advice='当前授权根再次核查仍无命中。停止当前缺失问题的查询，用1–2句话说明未找到，不遍历目录或读取无关节点。其他问题仍可正常检索。'
    elif not novel and not result.get('next_cursor'):
        stop='no_new_evidence'
        advice=('命中均已返回，没有新增候选，不要再换词搜索。若尚未核实正文，只补读最相关且未读的1–3项再回答；已经核对仍无依据的部分立即简短说明缺口，不重复读取。'
                if root_wide else '当前目录没有新增命中；确有必要可扩大范围，否则按已有依据回答，不重复读取。')
    else:
        advice='读取最相关的1–3项，按需补读条件和例外；有足够依据就回答并引用source.url。分页仍有新内容时可继续，整库审阅按用户要求执行。'
    state['last_search_empty_stop']=bool(stop=='no_relevant_evidence')
    return {**result,**({'stop_reason':stop} if stop else {}),'search_guidance':advice}


def record(cursor,snapshot_id,root,scope,root_wide,args,result):
    cursor.execute("SELECT to_regclass('public.kb_retrieval_states') IS NOT NULL AS enabled")
    if not cursor.fetchone()['enabled']:
        return result
    cursor.execute('SELECT state FROM public.kb_retrieval_states WHERE snapshot_id=%s FOR UPDATE',(snapshot_id,))
    row=cursor.fetchone()
    if not row:return result
    state=row['state'];result=feedback(state,root,scope,root_wide,args,result)
    cursor.execute('UPDATE public.kb_retrieval_states SET state=%s WHERE snapshot_id=%s',(Jsonb(state),snapshot_id))
    return result

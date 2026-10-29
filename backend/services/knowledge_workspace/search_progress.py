"""Per-snapshot evidence progress. A miss narrows advice, never authorization or recall."""
from psycopg.types.json import Jsonb

SOURCE_FACT_INSTRUCTIONS = '只陈述当前已读资料直接支持的事实；不要把界面字段、示例值或常识推成自动计算、默认值、校验行为、权限制度或普遍规则。缺少依据的解释不添加；用户明确要求推断时，标明推断并与原文分开。'

QUERY_INSTRUCTIONS = SOURCE_FACT_INSTRUCTIONS + '''正常命中的具体问题先直接回答结论，默认用一个短段落或2–3个短要点，通常2–4句话、正文约100–200字（来源链接不计）；这只是具体问题的默认目标，必要条件和例外较多时应完整保留。不要先总结后再分点重复同一结论；保留会改变处理结果的适用条件、必要步骤、期限、费用和例外，不因追求短而省略。合并多个来源的相同结论，不逐份复述、不介绍资料编号或检索过程，不额外展开用户未问的背景。来源只引用支撑当前结论的必要链接，链接文字用简短名称；同来源上下文已读全条件和例外时用本次已读来源，不按章节重复列链接，避免另列重复来源清单；系统仍会补充实际读取来源。影响当前结论的来源冲突要简短点明并请求确认，不能擅自选一方；与当前问题无关的差异不展开。测试或适用范围限制确实影响使用时简短保留，不写长篇免责声明。用户明确要求详细步骤、对比、完整清单或全部来源时按需充分回答，不强制套用简短格式。具体问题优先search mode=evidence直达正文；摘录仅作定位，回答前先read核对必要正文，不能只凭搜索片段作答；只有业务范围不明或问题需要目录分类时才route/outline。context=true一次补同来源的条件与例外，已涵盖提问就立即回答，不因为不同媒体重复出现或存在“冲突状态/人工备注”标题而继续阅读；标题或元数据不能视为实际业务冲突。具体问题不是整库审阅：目录和检索摘录用于定位，只读最相关的1–3项并按需补条件、例外；证据足够就回答，不为凑齐全框架未读数量继续读。找不到时先在当前授权根内扩大范围，根范围仍无命中可换一组核心词再查一次；不要把完整问句不断拆成更多关键词。search_guidance要求停止或没有新增证据时，停止当前缺失问题的检索，不再通过outline、逐个read或重复分页穷举；其他尚未处理的问题或授权根仍可正常查询。缺资料通常用1–2句话说明当前可访问范围没有找到，并只给一个必要的补充建议；即使历史对话已有答案，也不要复述其业务数值或要求用户确认是否沿用旧口径；未命中只代表本次范围缺少依据，不能推定旧规则已失效、被替换或仍有效。不输出工具名、状态码、查询清单、目录遍历、内部预算或长篇免责声明。部分命中时每个问题只说一次：有依据的直接给结论和来源，缺失的只说“当前资料未找到”并酌情给一个补充建议；不要写检索次数、完整范围名、关键词或重复来源一致的说明。只有用户明确要求整库审阅、全面汇总或全部来源时才遍历完整框架。'''


def feedback(state, root, scope, root_wide, args, result):
    """Only compare evidence searches in the same effective scope; pagination remains usable."""
    if args.get('mode','evidence')!='evidence':
        if result['items']:state['last_search_empty_stop']=False
        return {**result,'search_guidance':'目录定位不是正文依据。下一步在当前授权根或已定位scope中使用search mode=evidence直接查问题，再read最相关1–3项并设置context=true；不要read空目录或逐个outline。'}
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
        advice='检索摘录仅用于定位，回答业务问题前必须read最相关的1–3项（context=true），核对正文、条件和例外；已有正文依据才直接回答并逐字引用source.url，不改写链接参数。分页仍有新内容时可继续，整库审阅按用户要求执行。'
    if not result['items']:
        advice+=' 回复只说明当前授权范围缺少本问题的依据并酌情给一个补充建议；不要引用或逐项复述历史回答的天数、金额等数值，即使为了否定它们也不要复述。'
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

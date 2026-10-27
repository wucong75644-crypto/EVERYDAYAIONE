"""Literal per-file navigation terms. Never evidence or a substitute for reading."""
import hashlib
import re
from psycopg.types.json import Jsonb


def freeze(c,oid,files):
    c.execute("SELECT to_regprocedure('public.kb_file_directory(uuid,uuid,bigint,text)') IS NOT NULL AS enabled")
    if not c.fetchone()['enabled']:return
    from core.config import get_settings
    for file in files:
        if not get_settings().knowledge_media_enabled and file['parse_config']!='native-v1':continue
        c.execute('SELECT public.kb_file_directory(%s,%s,%s,%s) AS terms',(oid,file['binding_id'],file['revision'],file['parse_config']))
        file['directory_terms']=c.fetchone()['terms'] or []



def scoped_query(items,owner,query):
    """Do not repeatedly score an already satisfied literal ancestor qualifier."""
    by_id={i['id']:i for i in items if i['type']=='object'};chain=[];seen=set()
    while owner and owner['id'] not in seen:
        chain.append(owner);seen.add(owner['id']);owner=by_id.get(owner.get('parent_id'))
    for ancestor in reversed(chain):
        label=re.split(r'[（(\n:：]',ancestor['title'],maxsplit=1)[0].strip()
        for suffix in ('的','中','里'):
            prefix=label+suffix
            if label and query.startswith(prefix):query=query[len(prefix):].lstrip('的 ，,：:')
    return query

def grams(text):
    chinese=set()
    for word in re.findall(r'[\u3400-\u9fff]+',text):
        chinese.update(word[i:i+2] for i in range(len(word)-1))
    return chinese,set(re.findall(r'[a-z0-9][a-z0-9_.-]{1,}',text.lower()))


def route_score(items,item,query):
    """Score a next hop, exposing neither descendants nor their file text."""
    qcn,qother=grams(query)
    descendants={item['id']}
    if item['type']=='object':
        while True:
            extra={i['id'] for i in items if i['type']=='object' and i.get('parent_id') in descendants}
            if extra<=descendants:break
            descendants|=extra
    query_long={word[i:i+4] for word in re.findall(r'[\u3400-\u9fff]+',query) for i in range(len(word)-3)}
    prefix=query.strip()[:2]
    if prefix in {'根据','关于','请问','这个','这份','上述','已经','如果','没有','是否','是不'}:prefix=''
    best=0
    for child in items:
        if child['id'] not in descendants:continue
        if item['type']=='file' and child['handle']!=item['handle']:continue
        if child['type']=='file' and re.search(r'订单号|订单编号|单号|完整编号',query) and any(re.search(r'(?<!\d)\d{6}-\d{12,18}(?!\d)',t) for t in child.get('directory_terms',[])):
            best=max(best,4)  # A literal identifier shape is a route hint, never an answer.
        # A large branch must not win by combining unrelated words from many files.
        # Prefer a coherent title/row-name match; filenames remain literal metadata.
        for value,weight in [(child['title'],3),*[(t,1) for t in child.get('directory_terms',[])]]:
            cn,other=grams(value);matches=qcn&cn;tokens=qother&other
            starred=bool(re.match(r'^\*[^*\s]',value)) and ('星号' in query or '必填' in query) and bool(matches)
            if len(matches)<2 and not tokens and not starred:continue
            long={word[i:i+4] for word in re.findall(r'[\u3400-\u9fff]+',value) for i in range(len(word)-3)}
            best=max(best,weight*(len(matches)+len(tokens)*3+len(query_long&long)*4+8*starred+2*bool(prefix and value.lstrip('*# ').startswith(prefix))))
    return best



def collect_path(run,question):
    """Restore only this question's route; navigation permissions remain server-owned."""
    run.revalidate();key=hashlib.sha256(('directory-v4:'+question).encode()).hexdigest()
    with run.repo.cursor() as c:
        c.execute('SELECT state FROM public.kb_retrieval_states WHERE snapshot_id=%s',(run.id,));row=c.fetchone()
        saved=row['state'].get('directory_navigation') if row else None
    if saved and saved['question_hash']==key:return {**saved['plan'],'cache_hit':True}
    plan=_collect_path(run,question)
    run.revalidate()
    if plan.get('reason')=='hierarchical_route':
        with run.repo.cursor() as c:
            c.execute("UPDATE public.kb_retrieval_states SET state=jsonb_set(state,'{directory_navigation}',%s) WHERE snapshot_id=%s",(Jsonb({'question_hash':key,'plan':plan}),run.id))
    return plan


def question_parts(question):
    parts=[p.strip(' ，,。') for p in re.split(r'[？?；;]',question) if p.strip(' ，,。')]
    questions=[p for p in parts if re.search(r'哪|什么|多少|怎样|怎么|如何|是否|能否|可不|需不|几点|多久|可以|应该|为何|是不是',p)]
    if not 1<len(questions)<=3:return [question]
    return [questions[0],*[questions[0]+' '+p if re.match(r'^(?:截图|该图|其中|上述|这个|这项|它|新的|对应的|相关的)',p) else p for p in questions[1:]]]


def _collect_path(run,question):
    """Cover up to three explicit subquestions without reading any business text."""
    if len(run.snapshot['roots'])!=1:return {'mode':'expand','reason':'hierarchical_navigation'}
    parts=question_parts(question);limit=9 if len(parts)==1 else 21//len(parts)
    plans=[_route_one(run,part,limit) for part in parts]
    found=[p for p in plans if p.get('items')]
    if not found:return {'mode':'expand','reason':'hierarchical_navigation'}
    items=[];seen=set()
    for plan in found:
        for item in plan['items']:
            signature=tuple(item['next_step']['item_handles'])
            if signature not in seen:items.append(item);seen.add(signature)
    return {'mode':'expand','reason':'hierarchical_route','scope':'directory_only_partial',
            'path':[node for plan in found for node in plan['path']], 'items':items,
            'parts':parts,'unlocated_parts':[part for part,plan in zip(parts,plans) if not plan.get('items')]}


def _route_one(run,question,limit):
    root=run.snapshot['roots'][0]['handle'];scope=None;path=[];target=None;calls=0
    while calls<limit:
        args={'root_handle':root,'query':question,'limit':10}
        if scope:args['scope_handle']=scope
        result=run.execute('search',args);calls+=1
        if result.get('stop_reason') or not result.get('items'):break
        row=result['items'][0];path.append({'title':row['title'],'handle':row['handle'],'source':row['source']})
        target=row
        if row['type']=='file':break
        next_scope=row['handle']
        if next_scope==scope:break
        scope=next_scope
        if not row.get('has_children'):
            if calls<limit:
                result=run.execute('search',{'root_handle':root,'scope_handle':scope,'query':question,'limit':10});calls+=1
                file=next((i for i in result.get('items',[]) if i['type']=='file'),None)
                if file:target=file
            break
    if not target:return {'items':[],'path':[]}
    selected=next(i for i in run.snapshot['items'] if i['handle']==target['handle'])
    owner=next((i for i in run.snapshot['items'] if i['root_handle']==root and i['id']==selected['id'] and i['type']=='object'),None)
    # A requirement may live in an ancestor's actual title/body, not its leaf screenshot.
    matched=[]
    for node in path:
        item=next(i for i in run.snapshot['items'] if i['handle']==node['handle'])
        if item['type']=='object' and route_score([item],item,question):matched.append(item['handle'])
    handles=list(dict.fromkeys([*matched,*([owner['handle']] if owner else []),target['handle']]))
    return {'path':path,'items':[{'next_step':{'tool':'knowledge_read','root_handle':root,'item_handles':handles},'title':target['title'],'source':target['source'],'content_scope':'navigation_only'}]}

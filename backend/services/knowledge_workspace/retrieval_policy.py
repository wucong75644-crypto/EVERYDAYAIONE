"""Scope routing is server-owned: a handle narrows an existing root, never grants access."""
from hashlib import sha256
import re
from .query_terms import keywords


def subtree(items, scope):
    objects={i['id']:i for i in items if i['type']=='object'}
    selected={scope}
    while True:
        new={i['id'] for i in objects.values() if i.get('parent_id') in selected}
        if new<=selected:return selected
        selected|=new


def scoped(items, scope_handle=None, expand=False):
    if not scope_handle:return items,None
    owner=next((i for i in items if i['handle']==scope_handle and i['type']=='object'),None)
    if not owner:raise ValueError('KNOWLEDGE_INVALID_SCOPE')
    if expand and owner.get('parent_id'):
        parent=next((i for i in items if i['type']=='object' and i['id']==owner['parent_id']),None)
        if parent:owner=parent
    ids=subtree(items,owner['id'])
    return [i for i in items if i['id'] in ids],owner['handle']


def terms(query):
    values=[query.lower()]
    # Short lookup strings keep their phrase identity. Natural questions also
    # use Chinese terms; semantic recall remains a separate channel.
    if len(query)>8 or re.search(r'请|查询|查找|搜索|怎么|如何|什么|多少|哪些|是否|能否|吗',query):
        values.extend(keywords(query))
    for group in [('漏发','缺件','少收到','少发','补寄'),('退款','退钱'),('拼多多','pdd'),('淘宝','taobao')]:
        if any(v in query.lower() for v in group):values.extend(group)
    return list(dict.fromkeys(values))


def platform_allowed(query,text):
    query, text = query.casefold(), text.casefold()
    platforms = [group for group in (('拼多多','pdd'),('淘宝','taobao')) if any(p in query for p in group)]
    if platforms and not any(any(p in text for p in group) for group in platforms):
        return False
    # A precise mixed letter/number model or SKU must not be relaxed into a
    # semantically similar product. Several requested models may yield one each.
    identifiers = re.findall(r'(?<![a-z0-9_.-])[a-z][a-z0-9_.-]*\d[a-z0-9_.-]*(?![a-z0-9_.-])', query)
    return not identifiers or any(re.search(r'(?<![a-z0-9_.-])'+re.escape(value)+r'(?![a-z0-9_.-])', text) for value in identifiers)


def rank(query,title,text,path=''):
    terms_=terms(query);title=title.lower();text=text.lower();path=path.lower()
    score=sum(8*(t in title)+4*(t in path)+2*(t in text) for t in terms_)
    # Never drop an explicit platform constraint during synonym expansion.
    if not platform_allowed(query,title+path+text):return 0
    return score


def query_key(action,args):
    import json
    return sha256((action+json.dumps(args,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()

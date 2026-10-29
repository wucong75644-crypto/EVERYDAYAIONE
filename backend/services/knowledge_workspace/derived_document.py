"""One effective, provenance-preserving representation for UI and retrieval."""
from urllib.parse import urlencode


def effective_document(value, manual_labels=()):
    data = dict(value or {})
    overrides = data.get('overrides', {})
    sections, used = [], set()
    for section in data.get('sections', []):
        key = section['id']; used.add(key); change = overrides.get(key, {})
        if change.get('hidden'):
            continue
        sections.append({**section, **({'text':change['text'],'manual':True} if 'text' in change else {})})
    for key, change in overrides.items():
        if key.startswith('section:') and key not in used and not change.get('hidden') and isinstance(change.get('text'),str):
            sections.append({'id':key,'title':'人工更正（来源待核对）','text':change['text'],'source_refs':[], 'manual':True,'kind':'material'})
    tags = list(dict.fromkeys([*manual_labels, *[t for t in data.get('tags',[]) if not overrides.get('tag:'+t,{}).get('hidden')]]))
    from .grounding import VERSION
    original_keys={c.get('section_id') for c in data.get('chunks',[]) if c.get('kind')=='source'
                   and (not str(c.get('section_id','')).startswith('child:') or c.get('origin_version')==VERSION)}
    navigation_keys=original_keys|{'child:'+str(d['object_id']) for d in data.get('dependencies',[]) if 'binding_id' not in d}
    verified_sections=[]
    for section in sections:
        refs=list(dict.fromkeys(e['key'] for e in section.get('evidence',[]) if e.get('kind')!='metadata'))
        navigation=section.get('grounding_mode')=='navigation_fallback'
        if navigation:refs=list(dict.fromkeys(e['key'] for e in section.get('evidence',[]) if e.get('kind')=='metadata'))
        verified=bool(refs) and section.get('grounding_version')==VERSION and all(ref in (navigation_keys if navigation else original_keys) for ref in refs)
        if verified and refs and not section.get('manual'):section={**section,'source_refs':refs}
        state='manual' if section.get('manual') else 'unverified' if not verified else 'source' if section.get('grounding_mode')=='source' else 'navigation' if navigation else 'extractive' if section.get('grounding_mode')=='extractive_fallback' else 'verified'
        verified_sections.append({**section,'verification':state})
    sections=verified_sections
    # Hidden or overridden generated chunks must not remain independently searchable.
    chunks = [c for c in data.get('chunks', []) if c.get('kind')=='source' and not overrides.get(c.get('section_id'),{}).get('hidden')]
    original_chunks = {chunk['id']:chunk for chunk in data.get('chunks',[])}
    chunks += [{**({k:v for k,v in original_chunks.get(s['id'],{}).items() if k in ('embedding','embedding_model')} if not s.get('manual') else {}),'id':s['id'],'section_id':s['id'],'title':s['title'],'text':s['text'],'source_refs':s['source_refs'],'kind':'manual' if s.get('manual') else s['kind'],**({k:s[k] for k in ('evidence','grounding_version','grounding_mode','grounding_reason') if k in s} if not s.get('manual') else {})} for s in sections]
    return {**data,'sections':sections,'tags':tags,'auto_tags':[t for t in data.get('tags',[]) if t not in manual_labels and not overrides.get('tag:'+t,{}).get('hidden')],'chunks':chunks}


def source_links(data, object_id):
    links = {}
    for d in data.get('dependencies', []):
        q = {'object':d['object_id'],'revision':d['revision']}
        if 'binding_id' in d:
            q['binding']=d['binding_id'];key='file:'+d['binding_id']
        else:
            key=('body:' if d['object_id']==str(object_id) else 'child:')+d['object_id']
        if d.get('generation_id') and 'binding_id' not in d:
            q['generation'] = d['generation_id']
        # Prefer the exact generated child summary over its manual-body dependency.
        if key not in links or 'generation' in q:
            links[key]='/knowledge?'+urlencode(q)
        if 'binding_id' not in d:
            links['note:'+d['object_id']]=links[key]
    return links

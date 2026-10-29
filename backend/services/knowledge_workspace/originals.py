"""Canonical original spans survive rollups; generated prose is never re-ingested."""
import json
from hashlib import sha256


def origin(key, dependency=None):
    return json.dumps({'key':key,'version':dependency or {}},sort_keys=True,default=str,separators=(',',':'))


def merge(records):
    groups={}
    for record in records:
        if not record['text']:continue
        groups.setdefault(record['origin'],[]).append(record)
    result=[]
    for parts in groups.values():
        current=None
        for part in sorted(parts,key=lambda p:(p['start'], -len(p['text']))):
            part=dict(part)
            if current is None:current=part;continue
            offset=part['start']-current['start']
            tail=len(current['text'])-offset
            overlap=min(tail,len(part['text']))
            if overlap>=0:
                if current['text'][offset:offset+overlap]!=part['text'][:overlap]:
                    raise ValueError('ORIGINAL_SPAN_CONFLICT')
                current['text']+=part['text'][tail:]
            else:result.append(current);current=part
        if current:result.append(current)
    return result


def records(source):
    if '_originals' in source:return source['_originals']
    return [{'origin':origin(source['key'],source.get('dependency')),'start':0,'text':source.get('grounding_text',source['text'])}]


def manual_record(object_id,section_id,text):
    return {'origin':origin('override:'+section_id,{'object_id':str(object_id),'sha256':sha256(text.encode()).hexdigest()}),
            'start':0,'text':text}


def child_records(child,effective):
    from .grounding import VERSION
    result=[]
    if child['body'].strip():
        result.append({'origin':origin('body:'+str(child['id']),{'object_id':str(child['id']),'revision':child['revision']}),'start':0,'text':child['body']})
    for chunk in effective['chunks']:
        if chunk.get('kind')!='source' or str(chunk.get('section_id','')).startswith('child:') and chunk.get('origin_version')!=VERSION:continue
        provenance=chunk.get('provenance')
        if provenance:result.append({**provenance,'text':chunk['text']})
        else:
            # Compatibility for old immutable chunks: no summary or evidence quotes.
            result.append({'origin':origin('legacy:'+chunk['id'],{'object_id':str(child['id']),'generation_id':effective.get('generation_id')}),'start':0,'text':chunk['text']})
    for section in effective['sections']:
        if section.get('manual'):
            result.append(manual_record(child['id'],section['id'],section['text']))
    return merge(result)


def bound_sources(sources,limit):
    """Apply one shared limit to actual originals, not duplicate model/navigation fields."""
    result=[];remaining=limit;partial=False
    for source in sources:
        selected=[]
        for record in merge(records(source)):
            separator=2 if selected else 0
            available=max(0,remaining-separator)
            text=record['text'][:available];partial |= len(text)<len(record['text'])
            if text:
                selected.append({**record,'text':text});remaining-=len(text)+separator
        if selected or source['kind']=='child':
            text='\n\n'.join(r['text'] for r in selected)
            result.append({**source,'text':text,'_originals':selected,**({'grounding_text':text} if source['kind']=='child' else {})})
    return result,partial

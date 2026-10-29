"""Local node source projection. No descendant text and no model summarization."""
import asyncio
import json
from hashlib import sha256
from psycopg.types.json import Jsonb
from .chunking import source_chunks
from .grounding import VERSION


def enabled(repo):
    with repo.cursor() as c:
        c.execute("SELECT to_regprocedure('public.kb_local_pipeline()') IS NOT NULL AS enabled")
        return c.fetchone()['enabled']


async def project(repo,oid):
    from .derivation import inputs,guard_source
    from .extraction import media_config
    with repo.cursor() as c:
        c.execute('SELECT public.kb_claim_derivation(%s) AS value',(oid,));claim=c.fetchone()['value']
    if not claim:return None
    try:
        node,path,sources,deps,partial,fingerprint,pending=await asyncio.to_thread(inputs,repo,oid,include_children=False)
        if fingerprint!=claim['fingerprint']:raise ValueError('SOURCE_CHANGED')
        sections=[]
        for source in sources:
            for offset in range(0,len(source['text']),3000):
                text=source['text'][offset:offset+3000]
                if not text.strip():continue
                sections.append({'id':'section:'+sha256((source['key']+':'+json.dumps(source.get('dependency',{}),sort_keys=True,default=str)+':'+str(offset)).encode()).hexdigest()[:24],
                    'title':source['title'][:120],'text':text,'source_refs':[source['key']],'kind':'material',
                    'grounding_version':VERSION,'grounding_mode':'source',
                    'evidence':[{'key':source['key'],'quote':text,'kind':'original'}]})
        # Archive backup is not a failed document. Parsing state lives on each attachment.
        data={'sections':sections,'tags':[],'chunks':source_chunks(sources),'dependencies':deps,
              'coverage':'partial' if pending or partial else 'complete','config_version':'local-v1:'+media_config()}
        await asyncio.to_thread(guard_source,repo,oid,node['container_id'],fingerprint)
        with repo.cursor() as c:
            c.execute('SELECT public.kb_publish_derivation(%s,%s,%s,%s,%s) AS value',
                (oid,claim['token'],claim['dirty'],fingerprint,Jsonb(data)));return c.fetchone()['value']
    except asyncio.CancelledError:raise
    except Exception as error:
        with repo.cursor() as c:c.execute('SELECT public.kb_fail_derivation(%s,%s,%s)',(oid,claim['token'],type(error).__name__))
        if str(error) in ('SOURCE_CHANGED','PERMISSION_OR_SOURCE_CHANGED'):return None
        raise

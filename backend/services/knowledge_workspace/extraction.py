"""One effective view of immutable text extraction and versioned media recognition."""
from hashlib import sha256
from psycopg.types.json import Jsonb
from core.config import get_settings


def media_config():
    from .media import MEDIA_CONFIG_VERSION
    settings=get_settings()
    return sha256((MEDIA_CONFIG_VERSION+':'+settings.knowledge_vision_model+':'+settings.knowledge_asr_model).encode()).hexdigest()


def config_for(version):
    if version.get('_parse_config'):return version['_parse_config']
    return media_config() if version['mime_type'].startswith('image/') or version['mime_type'] in ('video/mp4','application/pdf') else 'native-v1'


def resolve(version,result):
    if not version:return version
    if version.get('text_status')=='ready' or not result:return version
    text=result.get('text')
    if not isinstance(text,str) or not text.strip() or result.get('coverage') not in ('complete','partial'):return version
    return {**version,'media_coverage':result['coverage'],'text_status':'ready' if result['coverage']=='complete' else 'truncated',
            **({'extracted_text':text} if 'extracted_text' in version else {})}


def hydrate(cursor,items):
    """Batch the already authorized (object, version) pairs; never use another config."""
    pairs=[{'object_id':str(oid),'binding_id':str(v['binding_id']),'revision':v['revision'],'config':config_for(v),'use_result':get_settings().knowledge_media_enabled or config_for(v)=='native-v1'} for oid,v in items
           if v and v.get('text_status')!='ready']
    if not pairs:return
    cursor.execute("SELECT to_regprocedure('public.kb_media_result(uuid,uuid,bigint,text)') IS NOT NULL AS enabled")
    if not cursor.fetchone()['enabled']:return
    cursor.execute('''SELECT p.object_id,p.binding_id,p.revision,
        CASE WHEN p.use_result THEN public.kb_media_result(p.object_id,p.binding_id,p.revision,p.config) ELSE NULL END AS result
        FROM jsonb_to_recordset(%s) AS p(object_id uuid,binding_id uuid,revision bigint,config text,use_result boolean)''',(Jsonb(pairs),))
    by_key={(str(r['object_id']),str(r['binding_id']),r['revision']):r['result'] for r in cursor.fetchall()}
    for oid,version in items:
        if version:
            value=resolve(version,by_key.get((str(oid),str(version['binding_id']),version['revision'])))
            if value is not version:version.update(value)

    cursor.execute("SELECT to_regprocedure('public.kb_file_state(uuid,uuid,bigint,text)') IS NOT NULL AS enabled")
    if cursor.fetchone()['enabled']:
        cursor.execute("""SELECT p.object_id,p.binding_id,p.revision,public.kb_file_state(p.object_id,p.binding_id,p.revision,p.config) AS job
            FROM jsonb_to_recordset(%s) AS p(object_id uuid,binding_id uuid,revision bigint,config text)""",(Jsonb(pairs),))
        jobs={(str(r['object_id']),str(r['binding_id']),r['revision']):r['job'] for r in cursor.fetchall()}
        for oid,v in items:
            if not v:continue
            job=jobs.get((str(oid),str(v['binding_id']),v['revision']))
            v['parse_state']='disabled' if not get_settings().knowledge_media_enabled and config_for(v)!='native-v1' else 'ready' if v['text_status']=='ready' else 'partial' if v['text_status']=='truncated' else 'unsupported' if by_key.get((str(oid),str(v['binding_id']),v['revision'])) else job['state'] if job else 'unsupported' if v['name'].lower().endswith(('.xmind','.zip','.pptx')) else 'queued'

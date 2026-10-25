"""Reviewed, restartable transfer into ordinary V3 documents; no model calls."""
from __future__ import annotations
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5
from psycopg.types.json import Jsonb
from .journal_privacy import SENSITIVE

ARCHIVE_TITLE = '旧库历史归档（待核对）'
LABELS = {'team_method':'待验证历史方法', 'team_history':'历史失败记录',
          'archive_global':'全局归属待核对', 'archive_config':'旧系统参数与模型历史',
          'archive_sensitive':'敏感原文隔离索引', 'archive_deleted':'已删除旧记录归档'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def decision(node, org):
    if node.get('org_id') not in (None, org):
        raise ValueError('OTHER_ORGANIZATION_REQUIRES_ITS_OWN_OPERATOR')
    if node.get('owner_user_id') or str(node.get('scope','')).startswith('user:'):
        raise ValueError('PERSONAL_OWNERSHIP_REQUIRES_SEPARATE_REVIEW')
    if node.get('is_deleted'):
        return 'archive_deleted'
    if SENSITIVE.search(node['title']+'\n'+node['content']):
        return 'archive_sensitive'
    if node['category'] != 'experience':
        return 'archive_config'
    if node.get('org_id') == org and node.get('scope') == 'org':
        return 'team_history' if node['node_type']=='failure_pattern' else 'team_method'
    return 'archive_global'


def groups(rows, org):
    buckets = defaultdict(list)
    for row in rows:
        n=row['node']; d=decision(n,org)
        # Exact duplicates only, never merge private/scope boundaries. Historic
        # configuration is grouped into bounded reference documents, not rules.
        key=(d,n.get('org_id'),n.get('scope'),n['category'])
        if d!='archive_config':
            key += (n['node_type'],n['title'],n['content'])
        buckets[digest(key)].append(row)
    result=[]
    for key,members in sorted(buckets.items()):
        members.sort(key=lambda r:r['node']['id'])
        d=decision(members[0]['node'],org)
        result.append({'key':key,'decision':d,'ids':[r['node']['id'] for r in members],
                       'fingerprints':[r['fingerprint'] for r in members]})
    offsets={'team_method':0,'team_history':4200,'archive_global':0,'archive_config':4200,'archive_sensitive':5200,'archive_deleted':6200}
    seen=Counter()
    for g in result:
        index=seen[g['decision']];seen[g['decision']]+=1
        g['position']={'x':700+400*(index//30),'y':offsets[g['decision']]+110*(index%30)}
    return result


def document(group, by_id):
    d=group['decision']; nodes=[by_id[i]['node'] for i in group['ids']]
    title=(LABELS[d]+' · '+nodes[0]['category']) if d=='archive_config' else nodes[0]['title']
    title=SENSITIVE.sub('[敏感信息已隔离]',title).strip()[:180] or LABELS[d]
    text=[f'记录类型：{LABELS[d]}',
          '迁移说明：原样保留历史资料，不代表当前规则、长期习惯或已验证成功的方法。历史任务不会因此重新执行。']
    if d=='team_history':text.append('历史状态：失败记录；不能将失败示例当成成功操作。')
    if d.startswith('archive_'):text.append('归档边界：限本项目管理者核对；不自动加入个人或团队默认检索范围。')
    if d=='archive_sensitive':text.append('敏感原文仅保留在服务器受限备份；本条为脱敏索引。')
    for n in nodes:
        text += [f"### {SENSITIVE.sub('[敏感信息已隔离]',n['title'])}",
                 SENSITIVE.sub('[敏感原文已隔离]',n['content']),
                 f"旧记录：{n['id']}；类型：{n['category']}/{n['node_type']}；来源：{n['source']}；原更新时间：{n['updated_at']}"]
    body='\n\n'.join(text)
    if len(body)>190000:raise ValueError('GROUP_TOO_LARGE_REVIEW_REQUIRED')
    return title,body


def read_sources(c):
    c.execute('SELECT to_jsonb(n) AS node,md5(to_jsonb(n)::text) AS fingerprint FROM public.knowledge_nodes n ORDER BY id')
    rows=c.fetchall()
    c.execute("SELECT count(*) AS count,md5(COALESCE(jsonb_agg(to_jsonb(e) ORDER BY id),'[]'::jsonb)::text) AS digest FROM public.knowledge_edges e")
    return rows,c.fetchone()


def validate_backup(path, rows, edge_count):
    root=Path(path); raw=(root/'manifest.json').read_bytes(); manifest=json.loads(raw)
    for name,expected in manifest['files'].items():
        if Path(name).name!=name:raise ValueError('UNSAFE_BACKUP_PATH')
        p=root/name
        if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            raise ValueError('BACKUP_INTEGRITY_FAILED')
    saved=json.loads((root/'source.json').read_text())
    if saved['knowledge_nodes']!=[r['node'] for r in rows] or len(saved['knowledge_edges'])!=edge_count:
        raise ValueError('BACKUP_SOURCE_CHANGED')
    return hashlib.sha256(raw).hexdigest()


def plan(c, actor, org, backup_path):
    c.execute('SELECT public.kb_actor_valid() AND public.reach_active_member(%s,true) AS allowed',(org,))
    if not c.fetchone()['allowed']:raise PermissionError('ORG_MANAGER_REQUIRED')
    rows,edges=read_sources(c)
    backup_sha=validate_backup(backup_path,rows,edges['count'])
    c.execute("SELECT id FROM public.kb_containers WHERE kind='team' AND org_id=%s AND deleted_at IS NULL AND NOT undo_hidden",(org,))
    team=c.fetchone()
    if not team:raise ValueError('TEAM_KNOWLEDGE_REQUIRED')
    source_digest=digest([(r['node']['id'],r['fingerprint']) for r in rows])
    return {'version':1,'actor':actor,'org':org,'backup_path':str(backup_path),'backup_sha256':backup_sha,
            'source_count':len(rows),'source_digest':source_digest,'edge_count':edges['count'],'edge_digest':edges['digest'],
            'team_id':str(team['id']),'groups':groups(rows,org)}, rows


def run_id(approved):
    return str(uuid5(NAMESPACE_URL,digest(approved)))


def command(c,rid,key,operation,payload):
    c.execute('SELECT public.kb_canvas_command(%s,%s,%s) AS result',(str(uuid5(UUID(rid),key)),operation,Jsonb(payload)))
    result=c.fetchone()['result']
    if 'object_id' not in result and operation in ('create_object','create_project'):
        raise RuntimeError('KNOWLEDGE_COMMAND_INCOMPLETE')
    return result


def create(c,rid,key,cid,parent,title,body='',kind='topic',index=0,position=None):
    return command(c,rid,key,'create_object',{'container_id':cid,'parent_id':parent,'kind':kind,'title':title,'body':body,
                                           'position':position or {'x':min(50000,360*(index//30)),'y':min(50000,90*(index%30))}})['object_id']


def setup(c,approved):
    rid=run_id(approved)
    c.execute('SELECT * FROM public.kb_legacy_import_runs WHERE id=%s FOR UPDATE',(rid,))
    previous=c.fetchone()
    if previous:
        if previous['state'].get('rolled_back'):raise ValueError('RUN_ROLLED_BACK')
        return rid,previous['state']
    c.execute('''INSERT INTO public.kb_legacy_import_runs(id,actor_id,org_id,source_digest,backup_manifest_sha256,backup_path,source_count,edge_count,edge_digest)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(rid,approved['actor'],approved['org'],approved['source_digest'],approved['backup_sha256'],approved['backup_path'],approved['source_count'],approved['edge_count'],approved['edge_digest']))
    result=command(c,rid,'archive','create_project',{'title':ARCHIVE_TITLE,'position':{'x':0,'y':0}})
    c.execute('SELECT container_id FROM public.kb_objects WHERE id=%s',(result['object_id'],))
    archive=str(c.fetchone()['container_id'])
    root=create(c,rid,'team-root',approved['team_id'],None,'旧库迁入资料（历史待核对）')
    state={'archive_container':archive,'archive_root':result['object_id'],'team_root':root,'parents':{}}
    for i,(d,label) in enumerate(LABELS.items()):
        cid=approved['team_id'] if d.startswith('team_') else archive
        parent=root if d.startswith('team_') else result['object_id']
        offsets={'team_method':0,'team_history':4200,'archive_global':0,'archive_config':4200,'archive_sensitive':5200,'archive_deleted':6200}
        state['parents'][d]=create(c,rid,'parent:'+d,cid,parent,label,position={'x':350,'y':offsets[d]})
    c.execute('UPDATE public.kb_legacy_import_runs SET state=%s WHERE id=%s',(Jsonb(state),rid))
    return rid,state


def apply_batch(c, approved, selected):
    # Freeze the source graph for this batch; telemetry uses separate tables.
    c.execute('LOCK TABLE public.knowledge_nodes,public.knowledge_edges IN SHARE MODE')
    current,rows=plan(c,approved['actor'],approved['org'],approved['backup_path'])
    if current!=approved:raise ValueError('SOURCE_OR_DESTINATION_CHANGED_REVIEW_AGAIN')
    rid,state=setup(c,approved); by_id={r['node']['id']:r for r in rows};saved=0
    for index,g in enumerate(selected):
        c.execute('SELECT legacy_id FROM public.kb_legacy_import_items WHERE legacy_id=ANY(%s::uuid[])',(g['ids'],))
        existing=c.fetchall()
        if existing:
            if len(existing)!=len(g['ids']):raise ValueError('INCOMPLETE_GROUP_MAPPING')
            continue
        title,body=document(g,by_id)
        cid=approved['team_id'] if g['decision'].startswith('team_') else state['archive_container']
        oid=create(c,rid,'item:'+g['key'],cid,state['parents'][g['decision']],title,body,'document',index,position=g['position'])
        c.execute('SELECT o.current_revision,o.parent_id,md5(v.body) AS hash FROM public.kb_objects o JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.id=%s',(oid,))
        obj=c.fetchone()
        for legacy_id,fingerprint in zip(g['ids'],g['fingerprints']):
            c.execute('''INSERT INTO public.kb_legacy_import_items(legacy_id,run_id,source_fingerprint,object_id,container_id,parent_id,imported_revision,body_hash,decision)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(legacy_id,rid,fingerprint,oid,cid,obj['parent_id'],obj['current_revision'],obj['hash'],g['decision']))
            saved+=1
    return {'run_id':rid,'records_saved':saved}



def graph_index(c,approved,rid):
    c.execute('SELECT state FROM public.kb_legacy_import_runs WHERE id=%s FOR UPDATE',(rid,))
    state=c.fetchone()['state']
    if state.get('edge_object_id'):return
    c.execute('SELECT legacy_id,object_id FROM public.kb_legacy_import_items WHERE run_id=%s',(rid,))
    mapped={str(x['legacy_id']):str(x['object_id']) for x in c.fetchall()}
    c.execute('SELECT id,source_id,target_id,relation_type,weight FROM public.knowledge_edges ORDER BY id')
    lines=['记录类型：旧关系索引（历史归档）','保留旧关系语义及新文档对应关系；不作为当前工具路由。完整原始元数据在受限备份中。']
    for e in c.fetchall():
        a,b=str(e['source_id']),str(e['target_id'])
        if a not in mapped or b not in mapped:raise ValueError('UNMAPPED_EDGE_ENDPOINT')
        lines.append(f"旧关系 {e['id']}：旧记录 {a} → {b}；关系 {e['relation_type']}；权重 {e['weight']}；[新来源](/knowledge?object={mapped[a]}) → [新目标](/knowledge?object={mapped[b]})")
    oid=create(c,rid,'edge-index',state['archive_container'],state['archive_root'],'旧库关系与迁移对应索引','\n\n'.join(lines),'document',position={'x':350,'y':8000})
    c.execute('SELECT md5(body) AS hash FROM public.kb_object_versions WHERE object_id=%s AND revision=1',(oid,))
    state.update(edge_object_id=oid,edge_hash=c.fetchone()['hash'],edge_revision=1)
    c.execute('UPDATE public.kb_legacy_import_runs SET state=%s WHERE id=%s',(Jsonb(state),rid))


def verify(c,approved,mark=False):
    current,rows=plan(c,approved['actor'],approved['org'],approved['backup_path'])
    if current!=approved:raise ValueError('SOURCE_CHANGED_REVIEW_AGAIN')
    rid=run_id(approved)
    c.execute('''SELECT i.*,o.deleted_at,o.undo_hidden,o.current_revision,o.parent_id AS live_parent,o.container_id AS live_container,
      md5(v.body) AS live_hash FROM public.kb_legacy_import_items i JOIN public.kb_objects o ON o.id=i.object_id
      JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE i.run_id=%s''',(rid,))
    imported=c.fetchall();by_id={str(r['legacy_id']):r for r in imported}
    errors=[]
    c.execute('SELECT state FROM public.kb_legacy_import_runs WHERE id=%s',(rid,))
    run=c.fetchone()
    if run:
        archive=run['state']['archive_container']
        c.execute("SELECT count(*) AS n FROM public.kb_container_members WHERE container_id=%s AND status='active' AND (user_id<>%s::uuid OR role<>'manager')",(archive,approved['actor']))
        if c.fetchone()['n']:errors.append('archive_acl_expanded')
        if run['state'].get('edge_object_id'):
            c.execute('SELECT o.current_revision,md5(v.body) AS hash FROM public.kb_objects o JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.id=%s AND o.deleted_at IS NULL AND NOT o.undo_hidden',(run['state']['edge_object_id'],))
            edge=c.fetchone()
            if not edge or edge['current_revision']!=run['state']['edge_revision'] or edge['hash']!=run['state']['edge_hash']:errors.append('edge_index_modified')
    else:errors.append('missing_import_run')
    c.execute('SELECT count(*) AS n FROM public.kb_legacy_import_items WHERE run_id=%s',(rid,))
    if c.fetchone()['n']!=len(imported):errors.append('destination_inaccessible')
    for row in rows:
        i=by_id.get(row['node']['id'])
        if not i or i['source_fingerprint']!=row['fingerprint']:errors.append('missing_or_changed_source');continue
        if i['deleted_at'] or i['undo_hidden'] or i['current_revision']!=i['imported_revision'] or i['live_hash']!=i['body_hash'] or i['live_parent']!=i['parent_id'] or i['live_container']!=i['container_id']:
            errors.append('destination_modified_or_deleted')
    if len(imported)!=len(rows):errors.append('mapping_count_mismatch')
    result={'run_id':rid,'source_records':len(rows),'mapped_records':len(imported),'documents':len({i['object_id'] for i in imported}),
            'decisions':dict(Counter(i['decision'] for i in imported)),'errors':dict(Counter(errors))}
    if mark:
        if errors:raise ValueError('RECONCILIATION_FAILED')
        graph_index(c,approved,rid)
        c.execute('UPDATE public.kb_legacy_import_runs SET verified_at=now() WHERE id=%s',(rid,))
    return result


def rollback(c,approved):
    result=verify(c,approved)
    if set(result['errors'])-{'missing_or_changed_source','mapping_count_mismatch'}:raise ValueError('ROLLBACK_WOULD_REMOVE_USER_CHANGES')
    rid=run_id(approved)
    c.execute('SELECT DISTINCT object_id,container_id,imported_revision FROM public.kb_legacy_import_items WHERE run_id=%s',(rid,))
    by_container=defaultdict(list)
    for row in c.fetchall():
        c.execute('SELECT 1 FROM public.kb_objects WHERE parent_id=%s AND deleted_at IS NULL AND NOT undo_hidden LIMIT 1',(row['object_id'],))
        if c.fetchone():raise ValueError('ROLLBACK_WOULD_REMOVE_USER_CHILDREN')
        by_container[str(row['container_id'])].append({'object_id':str(row['object_id']),'expected_revision':row['imported_revision']})
    c.execute('SELECT state FROM public.kb_legacy_import_runs WHERE id=%s FOR UPDATE',(rid,))
    state=c.fetchone()['state']
    if state.get('edge_object_id'):
        c.execute('SELECT o.current_revision,md5(v.body) AS hash FROM public.kb_objects o JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.id=%s',(state['edge_object_id'],))
        edge=c.fetchone()
        if not edge or edge['current_revision']!=state['edge_revision'] or edge['hash']!=state['edge_hash']:raise ValueError('ROLLBACK_WOULD_REMOVE_USER_CHANGES')
        by_container[state['archive_container']].append({'object_id':state['edge_object_id'],'expected_revision':edge['current_revision']})
    for cid,objects in by_container.items():
        c.execute('SELECT revision FROM public.kb_containers WHERE id=%s',(cid,));rev=c.fetchone()['revision']
        command(c,rid,'rollback:'+cid,'delete_branches',{'container_id':cid,'expected_container_revision':rev,'objects':objects})
    c.execute("UPDATE public.kb_legacy_import_runs SET state=state||'{\"rolled_back\":true}'::jsonb,verified_at=NULL WHERE id=%s",(rid,))
    return {'run_id':rid,'status':'rolled_back_recoverably'}

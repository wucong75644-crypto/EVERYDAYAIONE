"""Scoped reads and atomic database commands; no raw runtime table writes."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from core.db_scope import SET_DATABASE_SCOPE_SQL
from services.knowledge_workspace.files import public_version

class Repository:
    def __init__(self, pool, scope):
        self.pool, self.scope = pool, scope

    @contextmanager
    def cursor(self):
        with self.pool.connection() as connection, connection.transaction():
            with connection.cursor(row_factory=dict_row) as c:
                c.execute(SET_DATABASE_SCOPE_SQL, self.scope.settings)
                yield c

    def initialize(self):
        with self.cursor() as c:
            c.execute('SELECT public.kb_initialize()')

    def canvas(self, cursor=None):
        with self.cursor() as c:
            c.execute('''SELECT c.id,c.kind,c.title,c.revision,c.acl_revision,COALESCE(to_jsonb(c)->'canvas_theme','{}'::jsonb) AS canvas_theme,
                         public.kb_role(c.id) AS role FROM public.kb_containers c WHERE NOT COALESCE((to_jsonb(c)->>'undo_hidden')::boolean,false) ORDER BY c.id LIMIT 1001''')
            containers = c.fetchall()
            if len(containers) > 1000:
                raise ValueError('当前预览版本最多加载 1000 个空间')
            c.execute('''SELECT o.id,o.container_id,o.parent_id,o.kind,o.title,(to_jsonb(o)->>'sort_order')::bigint AS sort_order,o.current_revision AS revision,
                         n.id AS node_id,n.x,n.y,COALESCE(to_jsonb(n)->'canvas_data','{}'::jsonb) AS canvas_data,n.revision AS layout_revision
                         FROM public.kb_objects o JOIN public.kb_canvas_nodes n ON n.object_id=o.id
                         WHERE o.deleted_at IS NULL AND NOT COALESCE((to_jsonb(o)->>'undo_hidden')::boolean,false) AND NOT EXISTS(SELECT 1 FROM public.kb_containers c WHERE c.id=o.container_id AND COALESCE((to_jsonb(c)->>'undo_hidden')::boolean,false)) AND (%s::uuid IS NULL OR o.id>%s::uuid)
                         ORDER BY o.id LIMIT 501''', (cursor, cursor))
            rows = c.fetchall()
            # One permission-scoped batch for visible-page attachment summaries, never file bytes.
            c.execute("SELECT to_regprocedure('public.kb_file_version(uuid,uuid,bigint)') IS NOT NULL AS enabled")
            if c.fetchone()['enabled'] and rows:
                c.execute('''SELECT object_id,id,title,current_revision,total,
                             public.kb_file_version(object_id,id) AS version FROM (
                             SELECT b.*,count(*) OVER(PARTITION BY object_id) AS total,
                             row_number() OVER(PARTITION BY object_id ORDER BY id) AS rank
                             FROM public.kb_file_bindings b WHERE object_id=ANY(%s::uuid[])) files
                             WHERE rank<=2 ORDER BY object_id,id''', ([r['id'] for r in rows[:500]],))
                summaries = {}
                for item in c.fetchall():
                    summary = summaries.setdefault(item['object_id'], {'total':item['total'],'items':[]})
                    summary['items'].append({**{k:item[k] for k in ('id','title','current_revision')},'version':public_version(item['version'])})
                for row in rows:
                    row['attachments'] = summaries.get(row['id'], {'total':0,'items':[]})
            c.execute('''SELECT id,container_id,source_object_id,target_object_id,relation_type,COALESCE(to_jsonb(r)->'appearance','{}'::jsonb) AS appearance
                         FROM public.kb_relations r WHERE NOT EXISTS(SELECT 1 FROM public.kb_objects o JOIN public.kb_containers c ON c.id=o.container_id WHERE o.id IN (r.source_object_id,r.target_object_id) AND (COALESCE((to_jsonb(o)->>'undo_hidden')::boolean,false) OR COALESCE((to_jsonb(c)->>'undo_hidden')::boolean,false))) ORDER BY id LIMIT 2001''')
            relations = c.fetchall()
            if len(relations) > 2000:
                raise ValueError('当前预览版本最多加载 2000 条关联线')
            return {'containers': containers, 'nodes': rows[:500], 'relations': relations,
                    'next_cursor': str(rows[499]['id']) if len(rows)>500 else None}

    def object(self, object_id):
        with self.cursor() as c:
            c.execute('''SELECT o.id,o.container_id,o.parent_id,o.kind,o.title,o.current_revision AS revision,v.body,
                         public.kb_can(o.container_id,'write') AS can_write
                         FROM public.kb_objects o JOIN public.kb_object_versions v
                         ON v.object_id=o.id AND v.revision=o.current_revision
                         WHERE o.id=%s AND o.deleted_at IS NULL''', (object_id,))
            return c.fetchone()

    def command(self, command):
        payload = command.payload.model_dump(mode='json')
        if payload.get('manual_layout') is None:
            payload.pop('manual_layout', None)
        if getattr(command.payload, 'title_format', None) is not None:
            payload['title_format'] = command.payload.title_format.model_dump(mode='json', exclude_none=True)
        if payload.get('title_format') is None:
            payload.pop('title_format', None)
        if hasattr(command.payload, 'topics'):
            for topic, item in zip(payload['topics'], command.payload.topics):
                if item.title_format is None:
                    topic.pop('title_format', None)
                else:
                    topic['title_format'] = item.title_format.model_dump(mode='json', exclude_none=True)
        # Optional title layout fields must not alter receipts issued by older clients.
        if command.operation == 'create_object' and payload.get('before_object_id') is None:
            payload.pop('before_object_id', None)
        if command.operation in ('copy_branches','delete_branches'):
            for key in ('snapshot_positions','nodes'):
                if not payload.get(key): payload.pop(key, None)
        if command.operation == 'create_template':
            for key in ('nodes', 'after_object_id', 'before_object_id'):
                if not payload.get(key): payload.pop(key, None)
            payload['topics'] = [item.model_dump(mode='json', exclude_none=True) for item in command.payload.topics]
        if command.operation == 'update_canvas_data':
            if not payload.get('nodes'): payload.pop('nodes', None)
            payload['updates'] = [item.model_dump(mode='json', exclude_none=True) for item in command.payload.updates]
        if command.operation == 'update_title':
            payload = {key: value for key, value in payload.items() if value is not None}
        with self.cursor() as c:
            if command.operation == 'delete_project':
                c.execute('SELECT public.kb_delete_project(%s,%s) AS result',(command.command_id,Jsonb(payload)))
                return c.fetchone()['result']
            c.execute("SELECT to_regprocedure('public.kb_canvas_command(uuid,text,jsonb)') IS NOT NULL AS enabled")
            if c.fetchone()['enabled']:
                function = 'kb_canvas_restore' if command.operation in ('undo_canvas','redo_canvas') else 'kb_canvas_command'
                c.execute('SELECT public.' + function + '(%s,%s,%s) AS result', (command.command_id, command.operation, Jsonb(payload)))
                return c.fetchone()['result']
            if command.operation == 'move_branch':
                c.execute('SELECT public.kb_apply_branch_command(%s,%s) AS result',
                          (command.command_id, Jsonb(payload)))
                return c.fetchone()['result']
            c.execute('SELECT public.kb_apply_command(%s,%s,%s) AS result',
                      (command.command_id, command.operation, Jsonb(payload)))
            return c.fetchone()['result']

    def branch_snapshot(self, snapshot_id, payload=None):
        if payload is not None:
            payload = {key: value for key, value in payload.items() if key not in ('nodes','snapshot_positions') or value}
        with self.cursor() as c:
            c.execute('SELECT public.kb_branch_snapshot(%s,%s) AS result', (snapshot_id, Jsonb(payload) if payload is not None else None))
            return c.fetchone()['result']

    def canvas_history(self):
        with self.cursor() as c:
            c.execute("SELECT to_regprocedure('public.kb_canvas_history_list()') IS NOT NULL AS enabled")
            if not c.fetchone()['enabled']:
                return {'undo': None, 'redo': None}
            c.execute('SELECT public.kb_canvas_history_list() AS result')
            return c.fetchone()['result']

    def members(self, container_id):
        with self.cursor() as c:
            c.execute("SELECT public.kb_can(%s,'manage') AS allowed", (container_id,))
            if not c.fetchone()['allowed']:
                return None
            # All active org members may be selected, but only by a container manager.
            c.execute('''SELECT m.user_id,u.nickname,m.role AS org_role,
                         k.role,k.status FROM public.org_members m JOIN public.users u ON u.id=m.user_id
                         LEFT JOIN public.kb_container_members k ON k.user_id=m.user_id AND k.container_id=%s
                         WHERE m.org_id=%s AND m.status='active' AND u.status='active' ORDER BY m.user_id''',
                      (container_id,self.scope.org_id))
            return c.fetchall()

    def versions(self, object_id, before=None):
        with self.cursor() as c:
            c.execute("SELECT id FROM public.kb_objects WHERE id=%s AND deleted_at IS NULL", (object_id,))
            if c.fetchone() is None:
                return None
            c.execute('''SELECT revision,title,created_at FROM public.kb_object_versions
                         WHERE object_id=%s AND (%s::bigint IS NULL OR revision<%s::bigint)
                         ORDER BY revision DESC LIMIT 51''', (object_id, before, before))
            rows = c.fetchall()
            return {'items': rows[:50], 'next_cursor': rows[49]['revision'] if len(rows)>50 else None}

    def version(self, object_id, revision):
        with self.cursor() as c:
            c.execute('''SELECT v.object_id,v.revision,v.title,v.body,v.created_at
                         FROM public.kb_object_versions v JOIN public.kb_objects o ON o.id=v.object_id
                         WHERE v.object_id=%s AND v.revision=%s AND o.deleted_at IS NULL''',
                      (object_id, revision))
            return c.fetchone()

    def search(self, container_id, query, cursor=None):
        with self.cursor() as c:
            c.execute("SELECT to_regprocedure('public.kb_derived_document(uuid,uuid)') IS NOT NULL AS enabled")
            derived_enabled=c.fetchone()['enabled']
        from core.config import get_settings
        if derived_enabled and get_settings().knowledge_derivation_enabled:
            from .retrieval import search_nodes
            return search_nodes(self,container_id,query,cursor)
        # RLS is applied before a result can leave the database, including old ACL grants.
        # Literal substring matching also supports Chinese; %, _ are ordinary characters.
        with self.cursor() as c:
            c.execute("SELECT id FROM public.kb_containers c WHERE id=%s AND deleted_at IS NULL AND NOT COALESCE((to_jsonb(c)->>'undo_hidden')::boolean,false)", (container_id,))
            if c.fetchone() is None:
                return None
            c.execute('''SELECT o.id,o.title,o.kind,o.current_revision AS revision,
                         COALESCE(f.excerpt,substring(v.body FROM greatest(1,strpos(lower(v.body),lower(%s))-60) FOR 240)) AS excerpt
                         FROM public.kb_objects o JOIN public.kb_object_versions v
                           ON v.object_id=o.id AND v.revision=o.current_revision
                         LEFT JOIN LATERAL (SELECT public.kb_search_file(o.id,%s) AS excerpt) f ON true
                         WHERE o.container_id=%s AND o.deleted_at IS NULL AND NOT COALESCE((to_jsonb(o)->>'undo_hidden')::boolean,false)
                           AND (%s::uuid IS NULL OR o.id>%s::uuid)
                           AND (strpos(lower(o.title),lower(%s))>0 OR strpos(lower(v.body),lower(%s))>0 OR f.excerpt IS NOT NULL)
                         ORDER BY o.id LIMIT 51''', (query, query, container_id, cursor, cursor, query, query))
            rows = c.fetchall()
            return {'items': rows[:50], 'next_cursor': str(rows[49]['id']) if len(rows)>50 else None}

    def file_command(self, command_id, operation, payload):
        with self.cursor() as c:
            c.execute('SELECT public.kb_apply_file_command(%s,%s,%s) AS result',
                      (command_id, operation, Jsonb(payload)))
            return c.fetchone()['result']

    def files(self, object_id):
        with self.cursor() as c:
            c.execute('SELECT id FROM public.kb_objects WHERE id=%s AND deleted_at IS NULL', (object_id,))
            if c.fetchone() is None:
                return None
            c.execute('''SELECT b.id,b.title,b.current_revision,public.kb_file_version(b.object_id,b.id) AS version
                         FROM public.kb_file_bindings b WHERE b.object_id=%s ORDER BY b.id LIMIT 101''', (object_id,))
            return c.fetchall()

    def file_versions(self, object_id, binding_id, before=None):
        with self.cursor() as c:
            c.execute('SELECT public.kb_file_version(%s,%s) AS version', (object_id, binding_id))
            if c.fetchone()['version'] is None:
                return None
            c.execute('''SELECT public.kb_file_version(%s,binding_id,revision) AS version
                         FROM public.kb_file_binding_versions WHERE binding_id=%s
                         AND (%s::bigint IS NULL OR revision<%s::bigint) ORDER BY revision DESC LIMIT 51''',
                      (object_id, binding_id, before, before))
            rows = [r['version'] for r in c.fetchall()]
            return {'items': rows[:50], 'next_cursor': rows[49]['revision'] if len(rows)>50 else None}

    def file_version(self, object_id, binding_id, revision):
        with self.cursor() as c:
            c.execute('SELECT public.kb_file_version(%s,%s,%s) AS version', (object_id, binding_id, revision))
            return c.fetchone()['version']

    def document(self, object_id, generation_id=None):
        from .derived_document import effective_document, source_links
        obj=self.object(object_id)
        if not obj:return None
        from core.config import get_settings
        if generation_id is None and not get_settings().knowledge_derivation_enabled:
            return {**obj, 'derived': None}
        with self.cursor() as c:
            c.execute("SELECT to_regprocedure('public.kb_derived_document(uuid,uuid)') IS NOT NULL AS enabled")
            if not c.fetchone()['enabled']:return {**obj,'derived':None}
            c.execute('SELECT public.kb_derived_document(%s,%s) AS value',(object_id,generation_id));value=c.fetchone()['value']
            if value is None:return None
            c.execute('SELECT canvas_data FROM public.kb_canvas_nodes WHERE object_id=%s',(object_id,));row=c.fetchone()
            data=effective_document(value,(row or {}).get('canvas_data',{}).get('labels',[]))
            data['source_links']=source_links(data,object_id)
            return {**obj,'derived':data}

    def knowledge_override(self, object_id, key, value, revision, command_id):
        with self.cursor() as c:
            c.execute('SELECT public.kb_set_knowledge_override(%s,%s,%s,%s,%s) AS revision',(object_id,key,Jsonb(value),revision,command_id))
            return c.fetchone()

    def organize(self, container_id, cursor=None):
        """Explicit bounded retry/backfill; authorization is checked by each enqueue."""
        with self.cursor() as c:
            c.execute("SELECT id FROM public.kb_containers WHERE id=%s AND public.kb_can(id,'write') AND deleted_at IS NULL AND NOT undo_hidden",(container_id,))
            if not c.fetchone():return None
            c.execute("SELECT id FROM public.kb_objects WHERE container_id=%s AND deleted_at IS NULL AND NOT undo_hidden AND (%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT 51",(container_id,cursor,cursor))
            rows=c.fetchall()
            for row in rows[:50]:c.execute('SELECT public.kb_enqueue_derivation(%s)',(row['id'],))
            return {'queued':len(rows[:50]),'next_cursor':str(rows[49]['id']) if len(rows)>50 else None}

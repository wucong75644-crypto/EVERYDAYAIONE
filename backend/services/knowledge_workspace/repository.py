"""Scoped reads and atomic database commands; no raw runtime table writes."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from core.db_scope import SET_DATABASE_SCOPE_SQL

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
            c.execute('''SELECT c.id,c.kind,c.title,c.revision,c.acl_revision,
                         public.kb_role(c.id) AS role FROM public.kb_containers c ORDER BY c.id LIMIT 1001''')
            containers = c.fetchall()
            if len(containers) > 1000:
                raise ValueError('当前预览版本最多加载 1000 个空间')
            c.execute('''SELECT o.id,o.container_id,o.parent_id,o.kind,o.title,o.current_revision AS revision,
                         n.id AS node_id,n.x,n.y,n.revision AS layout_revision
                         FROM public.kb_objects o JOIN public.kb_canvas_nodes n ON n.object_id=o.id
                         WHERE o.deleted_at IS NULL AND (%s::uuid IS NULL OR o.id>%s::uuid)
                         ORDER BY o.id LIMIT 501''', (cursor, cursor))
            rows = c.fetchall()
            c.execute('''SELECT id,container_id,source_object_id,target_object_id,relation_type
                         FROM public.kb_relations ORDER BY id LIMIT 2001''')
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
        with self.cursor() as c:
            c.execute('SELECT public.kb_apply_command(%s,%s,%s) AS result',
                      (command.command_id, command.operation, Jsonb(command.payload.model_dump(mode='json'))))
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

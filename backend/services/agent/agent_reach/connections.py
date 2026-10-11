"""Organization-scoped connections and operation receipts, encrypted with the existing KEK service."""
from contextlib import contextmanager
from dataclasses import asdict
import json
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.db_scope import SET_DATABASE_SCOPE_SQL
from services.configuration.envelope import LocalKEKProvider, SecretEnvelope
from services.configuration.material_service import SecretMaterialService
from .contracts import ReachError

PUBLIC_COLUMNS = 'id,platform,account_id,display_name,status,credential_version,created_at,login_state,checked_at'


class Connections:
    def __init__(self, pool, scope, *, secrets=None):
        self.pool, self.scope = pool, scope
        self._secrets = secrets

    @contextmanager
    def cursor(self, *, admin=False):
        if not self.scope.org_id or not self.scope.actor_user_id:
            raise ReachError('AUTH_REQUIRED', '组织账号操作需要企业上下文')
        with self.pool.connection() as connection, connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(SET_DATABASE_SCOPE_SQL, self.scope.settings)
                cursor.execute('''SELECT m.role FROM public.org_members m
                    JOIN public.organizations o ON o.id=m.org_id
                    JOIN public.users u ON u.id=m.user_id
                    WHERE m.org_id=%s AND m.user_id=%s AND m.status='active'
                      AND o.status='active' AND u.status='active' ''',
                    (self.scope.org_id, self.scope.actor_user_id))
                member = cursor.fetchone()
                if not member or (admin and member['role'] not in {'owner', 'admin'}):
                    raise ReachError('ACCESS_DENIED', '没有组织账号管理或使用权限')
                yield cursor

    @property
    def secrets(self):
        if self._secrets is None:
            self._secrets = SecretMaterialService(LocalKEKProvider.from_environment())
        return self._secrets

    def list(self):
        with self.cursor() as cursor:
            cursor.execute(f'''SELECT {PUBLIC_COLUMNS} FROM public.reach_connections c
                WHERE c.org_id=%s AND c.status='active' AND (EXISTS (
                SELECT 1 FROM public.reach_connection_grants g WHERE g.connection_id=c.id
                AND g.org_id=c.org_id AND g.user_id=%s AND (g.can_read OR g.can_write)) OR EXISTS (
                SELECT 1 FROM public.org_members m WHERE m.org_id=c.org_id AND m.user_id=%s
                AND m.role IN ('owner','admin') AND m.status='active')) ORDER BY c.created_at''',
                (self.scope.org_id, self.scope.actor_user_id, self.scope.actor_user_id))
            return cursor.fetchall()

    def save(self, platform, account_id, display_name, payload):
        with self.cursor(admin=True) as cursor:
            connection_id = str(uuid4())
            envelope = self.secrets.encrypt_payload(scope_kind='organization', scope_id=self.scope.org_id,
                secret_name='agent_reach.' + connection_id, payload_version=1, payload=payload)
            cursor.execute(f'''INSERT INTO public.reach_connections
                (id,org_id,platform,account_id,display_name,secret_envelope,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {PUBLIC_COLUMNS}''',
                (connection_id, self.scope.org_id, platform, account_id, display_name,
                 Jsonb(asdict(envelope)), self.scope.actor_user_id))
            return cursor.fetchone()

    def assert_admin(self):
        with self.cursor(admin=True):
            pass

    def occupied_slots(self):
        with self.cursor(admin=True) as cursor:
            cursor.execute("SELECT id FROM public.reach_connections WHERE org_id=%s", (self.scope.org_id,))
            return {str(row['id']) for row in cursor.fetchall()}

    def admin_connection(self, connection_id):
        with self.cursor(admin=True) as cursor:
            cursor.execute('SELECT * FROM public.reach_connections WHERE id=%s AND org_id=%s',
                (connection_id, self.scope.org_id))
            row = cursor.fetchone()
            if not row:
                raise ReachError('NOT_FOUND', '组织连接不存在')
            return row

    def save_verified(self, platform, account_id, display_name, payload, *, connection_id=None, expected_version=None):
        if not isinstance(account_id, str) or not 1 <= len(account_id) <= 200:
            raise ReachError('UPSTREAM_CHANGED', '平台返回的账号ID无效')
        display_name = str(display_name)[:200]
        with self.cursor(admin=True) as cursor:
            # Serialize duplicate account connections across API processes.
            cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                (self.scope.org_id + ':' + platform + ':' + account_id,))
            if connection_id:
                cursor.execute('SELECT * FROM public.reach_connections WHERE id=%s AND org_id=%s FOR UPDATE',
                    (connection_id, self.scope.org_id))
                old = cursor.fetchone()
                if expected_version is not None and (not old or old['status'] != 'active'
                    or old['credential_version'] != expected_version):
                    raise ReachError('ACCESS_DENIED', '连接已断开或已变化，请重新开始连接')
                if old and (old['platform'] != platform or old['account_id'] != account_id):
                    raise ReachError('ACCOUNT_MISMATCH', '扫码账号与原连接不一致，请使用原账号重新授权')
            else:
                cursor.execute("SELECT * FROM public.reach_connections WHERE org_id=%s AND platform=%s AND account_id=%s AND status='active' FOR UPDATE",
                    (self.scope.org_id, platform, account_id))
                old = cursor.fetchone()
                connection_id = str(old['id']) if old else str(uuid4())
            envelope = self.secrets.encrypt_payload(scope_kind='organization',scope_id=self.scope.org_id,
                secret_name='agent_reach.'+str(connection_id),payload_version=1,payload=payload)
            if old:
                if old['status'] != 'active':
                    raise ReachError('ACCESS_DENIED', '该专用连接已撤销，请由运维重新分配扫码服务')
                cursor.execute(f"""UPDATE public.reach_connections SET secret_envelope=%s,
                    credential_version=credential_version+1,display_name=%s,login_state='connected',checked_at=now()
                    WHERE id=%s AND org_id=%s RETURNING {PUBLIC_COLUMNS}""",
                    (Jsonb(asdict(envelope)), display_name, connection_id,self.scope.org_id))
            else:
                cursor.execute(f"""INSERT INTO public.reach_connections
                    (id,org_id,platform,account_id,display_name,secret_envelope,created_by,login_state,checked_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'connected',now()) RETURNING {PUBLIC_COLUMNS}""",
                    (connection_id,self.scope.org_id,platform,account_id,display_name,Jsonb(asdict(envelope)),self.scope.actor_user_id))
            return cursor.fetchone()

    def record_health(self, connection_id, version, state):
        with self.cursor(admin=True) as cursor:
            cursor.execute("""UPDATE public.reach_connections SET login_state=%s,checked_at=now()
                WHERE id=%s AND org_id=%s AND credential_version=%s AND status='active'""",
                (state,connection_id,self.scope.org_id,version))

    def grant(self, connection_id, user_id, can_read, can_write):
        with self.cursor(admin=True) as cursor:
            cursor.execute('''SELECT id FROM public.reach_connections WHERE id=%s AND org_id=%s
                AND status='active' FOR UPDATE''', (connection_id, self.scope.org_id))
            if not cursor.fetchone():
                raise ReachError('NOT_FOUND', '组织连接不存在')
            cursor.execute('''SELECT user_id FROM public.org_members WHERE org_id=%s AND user_id=%s
                AND status='active' ''', (self.scope.org_id, user_id))
            if not cursor.fetchone():
                raise ReachError('ACCESS_DENIED', '只能授权给本组织活跃成员')
            cursor.execute('''INSERT INTO public.reach_connection_grants
                (org_id,connection_id,user_id,can_read,can_write) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(connection_id,user_id) DO UPDATE SET can_read=excluded.can_read,
                can_write=excluded.can_write''',
                (self.scope.org_id, connection_id, user_id, can_read, can_write))

    def revoke(self, connection_id):
        with self.cursor(admin=True) as cursor:
            cursor.execute('''UPDATE public.reach_connections SET status='revoked',secret_envelope=NULL,
                credential_version=credential_version+1 WHERE id=%s AND org_id=%s''',
                (connection_id, self.scope.org_id))

    def load(self, connection_id, platform, *, write=False):
        with self.cursor() as cursor:
            cursor.execute('''SELECT c.* FROM public.reach_connections c
                JOIN public.org_members m ON m.org_id=c.org_id AND m.user_id=%s AND m.status='active'
                LEFT JOIN public.reach_connection_grants g ON g.connection_id=c.id
                    AND g.org_id=c.org_id AND g.user_id=m.user_id
                WHERE c.id=%s AND c.org_id=%s AND c.platform=%s AND c.status='active'
                AND (m.role IN ('owner','admin') OR CASE WHEN %s THEN g.can_write ELSE g.can_read END)''',
                (self.scope.actor_user_id, connection_id, self.scope.org_id, platform, write))
            row = cursor.fetchone()
            if not row:
                raise ReachError('ACCESS_DENIED', '没有该组织账号的操作权限，或账号已撤销')
            envelope = SecretEnvelope(**row.pop('secret_envelope'))
            row['secret'] = self.secrets.decrypt_payload(envelope, scope_kind='organization',
                scope_id=self.scope.org_id, secret_name='agent_reach.' + str(row['id']))
            return row

    def reserve(self, call_id, connection, digest):
        with self.cursor() as cursor:
            # Refresh write authorization immediately before reserving dispatch.
            cursor.execute("""SELECT c.id FROM public.reach_connections c
                JOIN public.org_members m ON m.org_id=c.org_id AND m.user_id=%s AND m.status='active'
                LEFT JOIN public.reach_connection_grants g ON g.connection_id=c.id
                    AND g.org_id=c.org_id AND g.user_id=m.user_id
                WHERE c.id=%s AND c.org_id=%s AND c.status='active' AND c.credential_version=%s
                AND (m.role IN ('owner','admin') OR g.can_write)""",
                (self.scope.actor_user_id, connection['id'], self.scope.org_id, connection['credential_version']))
            if not cursor.fetchone():
                raise ReachError('ACCESS_DENIED', '写入权限或账号凭据已变化')
            # Uniqueness is durable across API workers and runtime restarts.
            cursor.execute('''INSERT INTO public.reach_operations
                (org_id,connection_id,actor_user_id,call_id,payload_digest,credential_version,state)
                VALUES (%s,%s,%s,%s,%s,%s,'executing') ON CONFLICT DO NOTHING RETURNING id''',
                (self.scope.org_id, connection['id'], self.scope.actor_user_id, call_id, digest,
                 connection['credential_version']))
            inserted = cursor.fetchone()
            if inserted:
                return str(inserted['id']), None
            cursor.execute('''SELECT id,state,receipt,payload_digest FROM public.reach_operations
                WHERE org_id=%s AND actor_user_id=%s AND call_id=%s''',
                (self.scope.org_id, self.scope.actor_user_id, call_id))
            existing = cursor.fetchone()
            if not existing or existing['payload_digest'] != digest:
                raise ReachError('REPLAY_CONFLICT', '操作内容已变化，不能复用原执行身份')
            return str(existing['id']), existing

    def settle(self, operation_id, state, receipt):
        with self.cursor() as cursor:
            cursor.execute('''UPDATE public.reach_operations SET state=%s,receipt=%s,updated_at=now()
                WHERE id=%s AND org_id=%s AND actor_user_id=%s AND state='executing' ''',
                (state, Jsonb(receipt), operation_id, self.scope.org_id, self.scope.actor_user_id))

    def operations(self):
        with self.cursor() as cursor:
            cursor.execute("""SELECT o.id,platform,account_id,o.state,o.receipt,o.created_at
                FROM public.reach_operations o JOIN public.reach_connections c ON c.id=o.connection_id
                WHERE o.org_id=%s AND o.actor_user_id=%s ORDER BY o.created_at DESC LIMIT 50""",
                (self.scope.org_id, self.scope.actor_user_id))
            return cursor.fetchall()

"""Optional integration test against the dedicated local Reach fixture database.

REACH_TEST_DSN must point to port 55479 on /private/tmp. Never uses DATABASE_URL.
The bootstrap fixture and migration are documented in the integration runbook.
"""
import os
from uuid import uuid4

import psycopg
from psycopg_pool import ConnectionPool
import pytest

from core.db_scope import DatabaseScope, DatabaseAccessKind
from services.agent.agent_reach.connections import Connections
from services.agent.agent_reach.contracts import ReachError
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService


@pytest.mark.skipif(not os.getenv('REACH_TEST_DSN'), reason='dedicated local PostgreSQL fixture not running')
def test_scoped_secrets_grants_receipts_and_revocation():
    dsn = os.environ['REACH_TEST_DSN']
    parsed = psycopg.conninfo.conninfo_to_dict(dsn)
    assert parsed.get('host') == '/private/tmp' and parsed.get('port') == '55479'
    org1, org2 = '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002'
    owner, outsider = '00000000-0000-0000-0000-000000000011', '00000000-0000-0000-0000-000000000012'
    member = str(uuid4())
    secrets = SecretMaterialService(LocalKEKProvider(current_version='test', keyring={'test':b'k'*32}))
    scope = lambda user, org: DatabaseScope(actor_user_id=user, org_id=org, access_kind=DatabaseAccessKind.RUNTIME)
    pool = ConnectionPool(dsn, kwargs={'options':'-c role=everydayai_runtime'})
    connection_id = None
    try:
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute("INSERT INTO users(id,status) VALUES (%s,'active')", (member,))
            db.execute("INSERT INTO org_members VALUES (%s,%s,'member','active')", (org1,member))
        admin = Connections(pool, scope(owner,org1), secrets=secrets)
        regular = Connections(pool, scope(member,org1), secrets=secrets)
        foreign = Connections(pool, scope(outsider,org2), secrets=secrets)
        row = admin.save('twitter','123','Test', {'auth_token':'dummy','ct0':'dummy'})
        connection_id = str(row['id'])
        assert 'secret_envelope' not in row
        assert regular.list() == [] and foreign.list() == []
        with pytest.raises(ReachError):
            regular.save('twitter','123','Test', {'token':'dummy'})
        with pytest.raises(ReachError):
            foreign.load(connection_id,'twitter')
        admin.grant(connection_id, member, True, False)
        assert regular.load(connection_id,'twitter')['secret']['ct0'] == 'dummy'
        version = row['credential_version']
        verified = admin.save_verified('twitter','123','Verified',{'auth_token':'new','ct0':'new'},
            connection_id=connection_id,expected_version=version)
        assert str(verified['id']) == connection_id and verified['login_state']=='connected'
        assert verified['credential_version']==version+1
        assert regular.load(connection_id,'twitter')['secret']['ct0']=='new'
        with pytest.raises(ReachError):
            admin.save_verified('twitter','other-account','Wrong',{'token':'x'},connection_id=connection_id,
                expected_version=verified['credential_version'])
        with pytest.raises(ReachError):
            admin.save_verified('twitter','123','Stale',{'token':'x'},connection_id=connection_id,expected_version=version)
        with pytest.raises(ReachError):
            regular.save_verified('twitter','123','Denied',{'token':'x'})
        repeated = admin.save_verified('twitter','123','Verified again',{'auth_token':'new','ct0':'new'})
        assert str(repeated['id'])==connection_id
        admin.record_health(connection_id,repeated['credential_version'],'expired')
        assert admin.list()[0]['login_state']=='expired'
        assert foreign.list()==[]
        with pytest.raises(ReachError):
            regular.load(connection_id,'twitter',write=True)
        with pytest.raises(ReachError):
            regular.reserve('denied-'+member,regular.load(connection_id,'twitter'),'a'*64)
        admin.grant(connection_id, member, True, True)
        connection = regular.load(connection_id,'twitter',write=True)
        operation_id, previous = regular.reserve('call-'+member, connection,'a'*64)
        assert previous is None
        _, replay = regular.reserve('call-'+member, connection,'a'*64)
        assert replay['state'] == 'executing'
        with pytest.raises(ReachError):
            regular.reserve('call-'+member,connection,'b'*64)
        regular.settle(operation_id,'succeeded',{'remote_id':'987'})
        assert regular.operations()[0]['receipt']['remote_id'] == '987'
        assert foreign.operations() == []
        admin.revoke(connection_id)
        with pytest.raises(ReachError):
            regular.load(connection_id,'twitter',write=True)
    finally:
        pool.close()
        with psycopg.connect(dsn, autocommit=True) as db:
            if connection_id:
                db.execute('DELETE FROM reach_operations WHERE connection_id=%s',(connection_id,))
                db.execute('DELETE FROM reach_connection_grants WHERE connection_id=%s',(connection_id,))
                db.execute('DELETE FROM reach_connections WHERE id=%s',(connection_id,))
            db.execute('DELETE FROM org_members WHERE user_id=%s',(member,))
            db.execute('DELETE FROM users WHERE id=%s',(member,))

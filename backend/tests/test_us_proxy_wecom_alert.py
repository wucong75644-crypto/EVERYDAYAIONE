import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
from core.local_db import LocalDBClient
from tests.test_chat_image_lifecycle_postgres import isolated_db


def module(name):
 spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[2]/'deploy/proxy'/name)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

a=module('wecom_alert.py');m=module('us_failover.py')

@pytest.mark.parametrize('status,kind,critical',[
 ({},'monitor_error',True),
 ({'checked_at':'2000-01-01T00:00:00+00:00'},'monitor_stale',True),
 ({'total':16,'healthy':16},None,False),
 ({'total':16,'healthy':1},'nodes_unavailable',False),
 ({'total':16,'healthy':0,'alert':'all_failed'},'all_failed',True),
])
def test_classification(status,kind,critical):
 now=datetime.now(timezone.utc)
 if status and 'checked_at' not in status:status={**status,'checked_at':now.isoformat()}
 assert a.classify(status,now)==(kind,critical)


def test_partial_recovery_ends_critical_outage_and_keeps_warning(tmp_path):
 now=datetime.now(timezone.utc)
 paths=a.advance(tmp_path,{'checked_at':now.isoformat(),'total':16,'healthy':0,'alert':'all_failed'},now,m.atomic_json)
 first=json.loads(paths[0].read_text());assert first['critical']
 later=now+timedelta(seconds=30)
 paths=a.advance(tmp_path,{'checked_at':later.isoformat(),'total':16,'healthy':1},later,m.atomic_json)
 events=[json.loads(path.read_text()) for path in paths]
 assert len(events)==2
 assert any(event.get('resolved_at') and event['id']==first['id'] for event in events)
 assert any(event['kind']=='nodes_unavailable' and not event['critical'] for event in events)


@pytest.fixture
def logs(isolated_db):
 with psycopg.connect(isolated_db) as conn:
  conn.execute((Path(__file__).parents[1]/'migrations/072_error_monitoring.sql').read_text())
 raw=LocalDBClient(isolated_db,min_size=1,max_size=2)
 yield raw,isolated_db
 raw.pool.close()


async def test_real_error_log_survives_send_failure_retry_and_recovery(logs,tmp_path):
 db,dsn=logs;now=datetime.now(timezone.utc)
 status={'checked_at':now.isoformat(),'total':16,'healthy':0,'alert':'all_failed'}
 paths=a.advance(tmp_path,status,now,m.atomic_json)
 sender=AsyncMock(side_effect=[False,True,True])
 recipient=lambda _:('admin','test-creds')
 with pytest.raises(RuntimeError,match='acknowledge'):
  await a.deliver_events(db,paths,m.atomic_json,sender,recipient)
 event=json.loads(paths[0].read_text())
 with psycopg.connect(dsn) as conn:
  assert conn.execute('SELECT occurrence_count,is_critical,is_resolved FROM error_logs WHERE id=%s',(event['log_id'],)).fetchone()==(1,True,False)
 assert not event.get('notified_failure')
 await a.deliver_events(db,paths,m.atomic_json,sender,recipient)
 await a.deliver_events(db,paths,m.atomic_json,sender,recipient)
 assert sender.await_count==2
 with psycopg.connect(dsn) as conn:
  assert conn.execute('SELECT count(*) FROM error_logs WHERE fingerprint=%s',(event['id'],)).fetchone()[0]==1
 later=now+timedelta(seconds=30)
 paths=a.advance(tmp_path,{'checked_at':later.isoformat(),'total':16,'healthy':16},later,m.atomic_json)
 await a.deliver_events(db,paths,m.atomic_json,sender,recipient)
 assert sender.await_count==3 and not list((tmp_path/'alert-events').glob('*.pending.json'))
 with psycopg.connect(dsn) as conn:
  assert conn.execute('SELECT count(*),bool_and(is_resolved) FROM error_logs WHERE fingerprint=%s',(event['id'],)).fetchone()==(1,True)


def test_state_saved_before_event_recovers_same_incident_id(tmp_path,monkeypatch):
 now=datetime.now(timezone.utc);status={'checked_at':now.isoformat(),'total':16,'healthy':0,'alert':'all_failed'}
 def crash(path,data):
  if str(path).endswith('.pending.json'):raise OSError('isolated crash')
  m.atomic_json(path,data)
 with pytest.raises(OSError):a.advance(tmp_path,status,now,crash)
 identity=json.loads((tmp_path/'alert-state.json').read_text())['active']
 paths=a.advance(tmp_path,status,now,m.atomic_json)
 assert json.loads(paths[0].read_text())['id']==identity

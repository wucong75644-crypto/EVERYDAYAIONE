"""Real disposable PostgreSQL: snapshot/notification state/outbox/message retry boundaries."""
import json
from uuid import uuid4
from datetime import timedelta

import pytest
from tests.test_scheduled_skill_postgres import configured, seed, environment, postgres_socket, MIGRATIONS  # noqa: F401
from tests.test_scheduled_task_upgrade_postgres import claim, start, taskrow
from tests.test_scheduled_monitoring import POLICY, NOW, row
from services.scheduler.monitoring import evaluate


@pytest.fixture
def monitor_db(configured):
    env=configured
    with env.pool.connection(privileged=True) as conn:
        conn.execute('CREATE TABLE conversations(id uuid PRIMARY KEY,user_id uuid,org_id uuid,last_message_preview text,message_count integer DEFAULT 0,updated_at timestamptz)')
        conn.execute("CREATE TABLE messages(id uuid PRIMARY KEY,conversation_id uuid,org_id uuid,role text,content jsonb,status text)")
        conn.execute("CREATE TABLE org_members(user_id uuid,org_id uuid,status text)")
        conn.execute((MIGRATIONS/'295_scheduled_conditional_notifications.sql').read_text())
        seed(conn,env,{'version':1,'skills':[]})
        env.conversation=uuid4()
        conn.execute('INSERT INTO conversations(id,user_id,org_id) VALUES(%s,%s,%s)',(env.conversation,env.actor,env.org))
        conn.execute("INSERT INTO org_members VALUES(%s,%s,'active')",(env.actor,env.org))
        conn.execute('UPDATE scheduled_tasks SET origin_conversation_id=%s,notification_policy=%s WHERE id=%s',(env.conversation,json.dumps(POLICY),env.task))
        yield conn,env


def complete(conn,env,observations,history,state):
    task=claim(conn,env.task,env.org,manual=True)
    token=task['run_token']
    started=start(conn,env.task,env.org,token)
    assert started['outcome']=='started'
    assert started['definition_snapshot']['origin_conversation_id']==str(env.conversation)
    decision=evaluate(POLICY,observations,history,state,NOW)
    deliveries=[]
    if decision['matches']:
        deliveries=[{'delivery_key':'web:self','delivery_kind':'result',
                     'target_context':{'type':'web','user_id':str(env.actor),'conversation_id':str(env.conversation)},
                     'payload':{'text':'匹配素材','content':[{'type':'text','text':'匹配素材'}]}}]
    result={'text':'完整报告','files':[],'observations':observations,'notification_decision':decision}
    commit=conn.execute("SELECT complete_scheduled_task_success(%s,%s,'active',NOW()+INTERVAL '1 day','report',%s,1,1,1,%s)",
                        (env.task,token,json.dumps(result),json.dumps(deliveries))).fetchone()[0]
    assert commit['outcome']=='completed'
    return decision,token,commit


def test_baseline_hit_duplicate_growth_persist_and_do_not_mutate_definition(monitor_db):
    conn,env=monitor_db
    initial=taskrow(conn,env.task)
    state={}
    history=[row(100,-30)]
    for sample,expected in [(row(100,-60),'baseline'),(row(700),'matched'),(row(700,30),'duplicate')]:
        decision,token,committed=complete(conn,env,[sample],history,state)
        assert decision['status']==expected
        state=decision['state'];history.append(sample)
        current=taskrow(conn,env.task)
        assert current['config_revision']==initial['config_revision']
        assert current['revision']==initial['revision']
        assert current['notification_state']==state
        run=conn.execute('SELECT observations,notification_decision,result_text FROM scheduled_task_runs WHERE id=%s',(token,)).fetchone()
        assert run[0]==[sample] and 'state' not in run[1]
        assert run[2]=='完整报告'
    assert conn.execute('SELECT count(*) FROM scheduled_task_deliveries').fetchone()[0]==1


def test_web_delivery_message_is_idempotent_and_durable_before_ack(monitor_db):
    conn,env=monitor_db
    decision,token,commit=complete(conn,env,[row(700)],[row(100,-60)],{})
    assert commit['push_status']=='queued'
    delivery=conn.execute('SELECT claim_scheduled_task_delivery(120,8)').fetchone()[0]
    args=(delivery['delivery_id'],delivery['lease_token'])
    assert conn.execute('SELECT save_scheduled_web_delivery(%s,%s)',args).fetchone()[0]['outcome']=='saved'
    assert conn.execute('SELECT save_scheduled_web_delivery(%s,%s)',args).fetchone()[0]['outcome']=='existing'
    assert conn.execute('SELECT count(*) FROM messages').fetchone()[0]==1
    assert conn.execute('SELECT message_count FROM conversations WHERE id=%s',(env.conversation,)).fetchone()[0]==1
    assert str(conn.execute('SELECT result_message_id FROM scheduled_task_runs WHERE id=%s',(token,)).fetchone()[0])==delivery['delivery_id']
    assert conn.execute('SELECT complete_scheduled_task_delivery(%s,%s)',args).fetchone()[0]['push_status']=='pushed'


@pytest.mark.parametrize('failure',['wrong_owner','wrong_org','deleted','inactive','expired'])
def test_web_delivery_rechecks_access_without_fallback(monitor_db,failure):
    conn,env=monitor_db
    complete(conn,env,[row(700)],[row(100,-60)],{})
    delivery=conn.execute('SELECT claim_scheduled_task_delivery(120,8)').fetchone()[0]
    if failure=='wrong_owner':conn.execute('UPDATE conversations SET user_id=%s',(uuid4(),))
    if failure=='wrong_org':conn.execute('UPDATE conversations SET org_id=%s',(uuid4(),))
    if failure=='deleted':conn.execute('DELETE FROM conversations')
    if failure=='inactive':conn.execute("UPDATE org_members SET status='inactive'")
    if failure=='expired':conn.execute("UPDATE scheduled_task_deliveries SET lease_expires_at=NOW()-INTERVAL '1 second'")
    result=conn.execute('SELECT save_scheduled_web_delivery(%s,%s)',(delivery['delivery_id'],delivery['lease_token'])).fetchone()[0]
    assert result['outcome'] in {'conversation_unavailable','membership_unavailable','ownership_lost'}
    assert conn.execute('SELECT count(*) FROM messages').fetchone()[0]==0


def test_changeset_commit_captures_new_fields_and_update_preserves(monitor_db):
    conn,env=monitor_db
    task=taskrow(conn,env.task)
    snapshot=conn.execute('SELECT scheduled_task_definition(t) FROM scheduled_tasks t WHERE id=%s',(env.task,)).fetchone()[0]
    snapshot.update({'name':'updated','interval_minutes':30,'schedule_type':'cron','cron_expr':'*/30 * * * *'})
    result=conn.execute("SELECT commit_scheduled_task_changeset(%s,%s,%s,%s,'update',%s,%s,%s)",
                        (uuid4(),env.org,env.actor,env.task,task['revision'],json.dumps(snapshot),str(uuid4()))).fetchone()[0]
    assert result['outcome']=='updated'
    current=taskrow(conn,env.task)
    assert current['origin_conversation_id']==str(env.conversation)
    assert current['notification_policy']==POLICY
    assert current['interval_minutes']==30
    assert current['config_revision']!=task['config_revision']
    assert conn.execute('SELECT definition_snapshot FROM scheduled_task_config_revisions WHERE id=%s',(current['config_revision'],)).fetchone()[0]['interval_minutes']==30

-- Use the existing scheduler table owner; production scheduler objects predate
-- everydayai_owner. Do not change ownership or broaden application grants.
DO $$ DECLARE scheduler_owner TEXT; BEGIN
 SELECT pg_get_userbyid(relowner) INTO STRICT scheduler_owner
 FROM pg_class WHERE oid='public.scheduled_tasks'::regclass;
 EXECUTE format('SET LOCAL ROLE %I', scheduler_owner);
END $$;
-- Additive upgrade. Existing tasks default to every-run delivery.
-- Apply with scheduled workers drained, as for immutable config snapshot upgrades.
DO $$ BEGIN
 LOCK TABLE public.scheduled_tasks IN ACCESS EXCLUSIVE MODE;
 IF EXISTS (SELECT 1 FROM public.scheduled_tasks WHERE status='running') THEN
  RAISE EXCEPTION 'SCHEDULED_NOTIFICATION_UPGRADE_REQUIRES_DRAIN';
 END IF;
END $$;
ALTER TABLE public.scheduled_tasks
 ADD COLUMN origin_conversation_id UUID,
 ADD COLUMN notification_policy JSONB NOT NULL DEFAULT '{"mode":"always"}',
 ADD COLUMN notification_state JSONB NOT NULL DEFAULT '{}',
 ADD COLUMN interval_minutes INTEGER;
ALTER TABLE public.scheduled_task_runs
 ADD COLUMN observations JSONB NOT NULL DEFAULT '[]',
 ADD COLUMN notification_decision JSONB,
 ADD COLUMN result_text TEXT;
ALTER TABLE public.scheduled_task_runs ADD COLUMN IF NOT EXISTS result_message_id UUID;


CREATE OR REPLACE FUNCTION public.scheduled_task_definition(p_task public.scheduled_tasks) RETURNS JSONB
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
    SELECT jsonb_object_agg(key,value) FROM jsonb_each(to_jsonb(p_task))
    WHERE key=ANY(ARRAY['id','org_id','user_id','name','prompt','cron_expr','schedule_type','weekdays',
        'day_of_month','run_at','timezone','push_target','template_file','max_credits','retry_count',
        'timeout_sec','execution_policy','plan_snapshot','data_scope','skill_revision_snapshot','origin_conversation_id','notification_policy','interval_minutes']);
$$;

CREATE OR REPLACE FUNCTION public.bump_scheduled_task_revision()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        NEW.schedule_enabled := NEW.status='active';
        RETURN NEW;
    END IF;
    IF OLD.status <> 'running' AND NEW.status='running' THEN
        NEW.run_token := gen_random_uuid();
        NEW.claimed_at := NOW();
        NEW.run_claim_revision := OLD.revision;
        NEW.run_previous_status := OLD.status;
    ELSIF NEW.status <> 'running' THEN
        IF OLD.status='running' AND NOT OLD.schedule_enabled THEN
            NEW.status := CASE WHEN OLD.run_previous_status='error' THEN 'error' ELSE 'paused' END;
            NEW.next_run_at := NULL;
        END IF;
        NEW.schedule_enabled := NEW.status='active';
        NEW.run_token := NULL;
        NEW.claimed_at := NULL;
        NEW.run_claim_revision := NULL;
        NEW.run_manual := FALSE;
        NEW.run_previous_status := NULL;
    END IF;
    -- Definition revision does not change on claim/result/progress bookkeeping.
    IF NEW.revision=OLD.revision AND
       (to_jsonb(NEW) - ARRAY['revision','status','next_run_at','run_token','claimed_at','run_manual',
        'config_revision','run_config_revision','retry_config_revision','run_previous_status','run_claim_revision','notification_state','last_run_at','last_summary','last_result','run_count','consecutive_failures','updated_at'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['revision','status','next_run_at','run_token','claimed_at','run_manual',
        'config_revision','run_config_revision','retry_config_revision','run_previous_status','run_claim_revision','notification_state','last_run_at','last_summary','last_result','run_count','consecutive_failures','updated_at']) THEN
        NEW.revision := OLD.revision+1;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.commit_scheduled_task_changeset(
    p_change_set_id UUID,
    p_org_id UUID,
    p_user_id UUID,
    p_task_id UUID,
    p_operation TEXT,
    p_base_revision BIGINT,
    p_definition JSONB,
    p_idempotency_key TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task public.scheduled_tasks%ROWTYPE;
    v_receipt public.scheduled_task_change_receipts%ROWTYPE;
    v_new_revision BIGINT;
    v_result JSONB;
    v_next_run_at TIMESTAMPTZ;
    v_weekdays SMALLINT[];
BEGIN
    IF p_operation NOT IN ('create','update','pause','resume','delete')
       OR p_definition IS NULL OR jsonb_typeof(p_definition) <> 'object'
       OR COALESCE(p_idempotency_key, '') = '' THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_CHANGE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_receipt
      FROM public.scheduled_task_change_receipts
     WHERE org_id = p_org_id AND idempotency_key = p_idempotency_key
     FOR UPDATE;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'outcome', 'duplicate', 'task_id', v_receipt.task_id,
            'new_revision', v_receipt.new_revision, 'result', v_receipt.result
        );
    END IF;

    IF p_operation = 'create' THEN
        IF p_base_revision <> 0 THEN
            RETURN jsonb_build_object('outcome', 'conflict', 'reason', 'create_base_revision_must_be_zero');
        END IF;
        INSERT INTO public.scheduled_tasks(
            id, org_id, user_id, name, prompt, cron_expr, schedule_type,
            weekdays, day_of_month, run_at, timezone, push_target, template_file,
            status, max_credits, retry_count, timeout_sec, next_run_at,
            run_count, consecutive_failures, execution_policy, plan_snapshot, data_scope, revision, skill_revision_snapshot, origin_conversation_id, notification_policy, interval_minutes
        ) VALUES (
            p_task_id, p_org_id, p_user_id, p_definition->>'name', p_definition->>'prompt',
            NULLIF(p_definition->>'cron_expr',''), p_definition->>'schedule_type',
            CASE WHEN jsonb_typeof(p_definition->'weekdays') = 'array'
                 THEN ARRAY(SELECT jsonb_array_elements_text(p_definition->'weekdays')::smallint)
                 ELSE NULL END,
            NULLIF(p_definition->>'day_of_month','')::smallint,
            NULLIF(p_definition->>'run_at','')::timestamptz,
            COALESCE(p_definition->>'timezone', 'Asia/Shanghai'),
            p_definition->'push_target', p_definition->'template_file', 'active',
            COALESCE((p_definition->>'max_credits')::integer, 10),
            COALESCE((p_definition->>'retry_count')::smallint, 1),
            COALESCE((p_definition->>'timeout_sec')::integer, 180),
            NULLIF(p_definition->>'next_run_at','')::timestamptz,
            0, 0, p_definition->'execution_policy', p_definition->'plan_snapshot',
            COALESCE(p_definition->'data_scope', '{"kind":"task_prompt"}'::JSONB), 1,
            COALESCE(p_definition->'skill_revision_snapshot','{"version":1,"skills":[]}'::jsonb),
            NULLIF(p_definition->>'origin_conversation_id','')::uuid,
            COALESCE(p_definition->'notification_policy','{"mode":"always"}'::jsonb),
            NULLIF(p_definition->>'interval_minutes','')::integer
        ) RETURNING * INTO v_task;
        v_new_revision := v_task.revision;
        v_result := jsonb_build_object('outcome','created','task_id',v_task.id,'new_revision',v_new_revision);
    ELSE
        SELECT * INTO v_task
          FROM public.scheduled_tasks
         WHERE id = p_task_id AND org_id = p_org_id
         FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('outcome', 'conflict', 'reason', 'task_not_found');
        END IF;
        IF v_task.revision <> p_base_revision THEN
            RETURN jsonb_build_object(
                'outcome', 'conflict', 'reason', 'base_revision_mismatch',
                'current_revision', v_task.revision, 'base_revision', p_base_revision
            );
        END IF;
        IF v_task.status = 'running' AND p_operation NOT IN ('pause','resume') THEN
            RETURN jsonb_build_object('outcome', 'conflict', 'reason', 'task_running');
        END IF;

        IF p_operation = 'delete' THEN
            v_new_revision := v_task.revision + 1;
            DELETE FROM public.scheduled_tasks WHERE id = v_task.id;
            v_result := jsonb_build_object('outcome','deleted','task_id',v_task.id,'new_revision',v_new_revision);
        ELSE
            IF p_operation = 'pause' THEN
                UPDATE public.scheduled_tasks
                   SET schedule_enabled = FALSE, run_previous_status = 'paused',
                       status = CASE WHEN status='running' THEN 'running' ELSE 'paused' END,
                       next_run_at = NULL, revision = revision + 1,
                       updated_at = NOW()
                 WHERE id = v_task.id
                 RETURNING * INTO v_task;
                v_result := jsonb_build_object('outcome','paused','task_id',v_task.id,'new_revision',v_task.revision);
            ELSIF p_operation = 'resume' THEN
                v_next_run_at := NULLIF(p_definition->>'next_run_at','')::timestamptz;
                IF v_next_run_at IS NULL THEN
                    RAISE EXCEPTION 'SCHEDULED_TASK_RESUME_TIME_REQUIRED' USING ERRCODE = '22023';
                END IF;
                UPDATE public.scheduled_tasks
                   SET schedule_enabled = TRUE,
                       status = CASE WHEN status='running' THEN 'running' ELSE 'active' END,
                       next_run_at = CASE WHEN status='running' THEN NULL ELSE v_next_run_at END,
                       consecutive_failures = 0, revision = revision + 1,
                       updated_at = NOW()
                 WHERE id = v_task.id
                 RETURNING * INTO v_task;
                v_result := jsonb_build_object('outcome','resumed','task_id',v_task.id,'new_revision',v_task.revision);
            ELSE
                UPDATE public.scheduled_tasks
                   SET origin_conversation_id = NULLIF(p_definition->>'origin_conversation_id','')::uuid,
                       notification_policy = COALESCE(p_definition->'notification_policy','{"mode":"always"}'::jsonb),
                       interval_minutes = NULLIF(p_definition->>'interval_minutes','')::integer,
                       name = p_definition->>'name',
                       prompt = p_definition->>'prompt',
                       cron_expr = NULLIF(p_definition->>'cron_expr',''),
                       schedule_type = p_definition->>'schedule_type',
                       weekdays = CASE WHEN jsonb_typeof(p_definition->'weekdays') = 'array'
                                      THEN ARRAY(SELECT jsonb_array_elements_text(p_definition->'weekdays')::smallint)
                                      ELSE NULL END,
                       day_of_month = NULLIF(p_definition->>'day_of_month','')::smallint,
                       run_at = NULLIF(p_definition->>'run_at','')::timestamptz,
                       timezone = COALESCE(p_definition->>'timezone','Asia/Shanghai'),
                       push_target = p_definition->'push_target',
                       template_file = p_definition->'template_file',
                       max_credits = COALESCE((p_definition->>'max_credits')::integer, 10),
                       retry_count = COALESCE((p_definition->>'retry_count')::smallint, 1),
                       timeout_sec = COALESCE((p_definition->>'timeout_sec')::integer, 180),
                       next_run_at = NULLIF(p_definition->>'next_run_at','')::timestamptz,
                       skill_revision_snapshot = COALESCE(p_definition->'skill_revision_snapshot','{"version":1,"skills":[]}'::jsonb),
                       execution_policy = p_definition->'execution_policy',
                       plan_snapshot = p_definition->'plan_snapshot',
                       data_scope = COALESCE(p_definition->'data_scope', '{"kind":"task_prompt"}'::JSONB),
                       consecutive_failures = 0, revision = revision + 1,
                       updated_at = NOW()
                 WHERE id = v_task.id
                 RETURNING * INTO v_task;
                v_result := jsonb_build_object('outcome','updated','task_id',v_task.id,'new_revision',v_task.revision);
            END IF;
            v_new_revision := v_task.revision;
        END IF;
    END IF;

    INSERT INTO public.scheduled_task_change_receipts(
        org_id, change_set_id, idempotency_key, task_id, operation,
        base_revision, new_revision, result
    ) VALUES (
        p_org_id, p_change_set_id, p_idempotency_key, p_task_id, p_operation,
        p_base_revision, v_new_revision, v_result
    );
    RETURN v_result || jsonb_build_object('task', CASE WHEN p_operation = 'delete' THEN NULL ELSE to_jsonb(v_task) END);
END;
$$;

CREATE OR REPLACE FUNCTION complete_scheduled_task_success(
    p_task_id UUID,
    p_run_id UUID,
    p_next_status TEXT,
    p_next_run_at TIMESTAMPTZ,
    p_last_summary TEXT,
    p_last_result JSONB,
    p_credits_used INTEGER,
    p_tokens_used INTEGER,
    p_duration_ms INTEGER,
    p_deliveries JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task scheduled_tasks%ROWTYPE;
    v_run scheduled_task_runs%ROWTYPE;
    v_delivery JSONB;
    v_push_status TEXT;
BEGIN
    IF p_next_status NOT IN ('active', 'paused', 'error')
       OR p_last_result IS NULL OR jsonb_typeof(p_last_result) <> 'object'
       OR p_deliveries IS NULL OR jsonb_typeof(p_deliveries) <> 'array'
       OR p_credits_used < 0 OR p_tokens_used < 0 OR p_duration_ms < 0 THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_SUCCESS_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    -- All lifecycle operations lock task before run, including recovery.
    SELECT * INTO v_task FROM scheduled_tasks WHERE id=p_task_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome','task_missing');
    END IF;
    SELECT * INTO v_run FROM scheduled_task_runs
      WHERE id=p_run_id AND task_id=p_task_id AND org_id=v_task.org_id FOR UPDATE;
    IF NOT FOUND OR v_run.status <> 'running'
       OR (v_task.run_token IS NOT NULL AND v_task.run_token <> p_run_id) THEN
        RETURN jsonb_build_object('outcome','run_not_running');
    END IF;
    IF NOT v_task.schedule_enabled THEN
        p_next_status := CASE WHEN v_task.run_previous_status='error' THEN 'error' ELSE 'paused' END;
        p_next_run_at := NULL;
    ELSIF v_task.schedule_type='once' THEN
        p_next_status := 'paused';
        p_next_run_at := NULL;
    ELSE
        p_next_status := 'active';
    END IF;

    FOR v_delivery IN SELECT value FROM jsonb_array_elements(p_deliveries) LOOP
        IF jsonb_typeof(v_delivery) <> 'object'
           OR COALESCE(v_delivery->>'delivery_key', '') = ''
           OR v_delivery->>'delivery_kind' <> 'result'
           OR jsonb_typeof(v_delivery->'target_context') <> 'object'
           OR jsonb_typeof(v_delivery->'payload') <> 'object'
           OR v_delivery->'target_context'->>'type' NOT IN ('wecom_user', 'wecom_group', 'web')
           OR (v_delivery->'target_context'->>'type' <> 'web' AND COALESCE(v_delivery->'target_context'->>'chatid', '') = '')
           OR (v_delivery->'target_context'->>'type' = 'web' AND
               (COALESCE(v_delivery->'target_context'->>'user_id','') = '' OR
                COALESCE(v_delivery->'target_context'->>'conversation_id','') = '')) THEN
            RAISE EXCEPTION 'SCHEDULED_TASK_DELIVERY_INVALID'
                USING ERRCODE = '22023';
        END IF;

        INSERT INTO scheduled_task_deliveries(
            run_id, task_id, org_id, delivery_key, delivery_kind,
            target_context, payload
        ) VALUES (
            p_run_id, p_task_id, v_task.org_id,
            v_delivery->>'delivery_key', 'result',
            v_delivery->'target_context', v_delivery->'payload'
        ) ON CONFLICT (run_id, delivery_key) DO NOTHING;
    END LOOP;

    UPDATE scheduled_tasks
       SET retry_config_revision = NULL, status = CASE WHEN v_task.status = 'running' THEN p_next_status ELSE v_task.status END,
           next_run_at = CASE
               WHEN v_task.status = 'running' THEN p_next_run_at
               ELSE v_task.next_run_at
           END,
           last_run_at = NOW(),
           last_summary = p_last_summary,
           last_result = (p_last_result - 'observations' - 'text' - 'notification_decision') ||
               CASE WHEN p_last_result ? 'notification_decision'
                    THEN jsonb_build_object('notification_decision', (p_last_result->'notification_decision') - 'state')
                    ELSE '{}'::jsonb END,
           notification_state = COALESCE(p_last_result->'notification_decision'->'state', notification_state),
           run_count = v_task.run_count + 1,
           consecutive_failures = 0,
           updated_at = NOW()
     WHERE id = p_task_id;

    UPDATE scheduled_task_runs
       SET status = 'success',
           result_text = p_last_result->>'text',
           observations = COALESCE(p_last_result->'observations','[]'::jsonb),
           notification_decision = (p_last_result->'notification_decision') - 'state',
           result_summary = p_last_summary,
           result_files = COALESCE(p_last_result->'files', '[]'::JSONB),
           credits_used = p_credits_used,
           tokens_used = p_tokens_used,
           duration_ms = p_duration_ms,
           finished_at = NOW()
     WHERE id = p_run_id;

    v_push_status := refresh_scheduled_task_run_push_status(p_run_id);
    RETURN jsonb_build_object(
        'outcome', 'completed',
        'push_status', v_push_status, 'next_run_at', p_next_run_at, 'schedule_status', p_next_status
    );
END;
$$;


-- Web messages use the existing delivery lease and the messages table.
-- A fixed message UUID (delivery UUID) makes retry after a process crash idempotent.
CREATE OR REPLACE FUNCTION public.save_scheduled_web_delivery(p_delivery_id UUID, p_lease_token UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE d scheduled_task_deliveries%ROWTYPE; c conversations%ROWTYPE; inserted UUID;
BEGIN
 SELECT * INTO d FROM scheduled_task_deliveries WHERE id=p_delivery_id FOR UPDATE;
 IF NOT FOUND OR d.status <> 'delivering' OR d.lease_token IS DISTINCT FROM p_lease_token
    OR d.lease_expires_at <= NOW() THEN
  RETURN jsonb_build_object('outcome','ownership_lost');
 END IF;
 IF d.target_context->>'type' <> 'web' THEN
  RAISE EXCEPTION 'SCHEDULED_WEB_TARGET_INVALID';
 END IF;
 -- Recheck ownership at delivery time. No fallback into another conversation.
 SELECT * INTO c FROM conversations
 WHERE id=(d.target_context->>'conversation_id')::uuid AND org_id=d.org_id
 AND user_id=(d.target_context->>'user_id')::uuid FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','conversation_unavailable'); END IF;
 IF NOT EXISTS (SELECT 1 FROM org_members WHERE org_id=d.org_id AND user_id=c.user_id AND status='active') THEN
  RETURN jsonb_build_object('outcome','membership_unavailable');
 END IF;
 INSERT INTO messages(id,conversation_id,org_id,role,content,status)
 VALUES(d.id,c.id,d.org_id,'assistant',d.payload->'content','completed')
 ON CONFLICT(id) DO NOTHING RETURNING id INTO inserted;
 IF inserted IS NULL AND NOT EXISTS (SELECT 1 FROM messages WHERE id=d.id AND conversation_id=c.id AND org_id=d.org_id) THEN
  RAISE EXCEPTION 'SCHEDULED_WEB_MESSAGE_CONFLICT';
 END IF;
 IF inserted IS NOT NULL THEN
  UPDATE conversations SET last_message_preview=LEFT(d.payload->>'text',100),
   message_count=COALESCE(message_count,0)+1, updated_at=NOW() WHERE id=c.id;
 END IF;
 UPDATE scheduled_task_runs SET result_message_id=d.id WHERE id=d.run_id AND org_id=d.org_id;
 RETURN jsonb_build_object('outcome',CASE WHEN inserted IS NULL THEN 'existing' ELSE 'saved' END,
                          'message_id',d.id,'conversation_id',c.id);
END $$;
REVOKE ALL ON FUNCTION public.save_scheduled_web_delivery(UUID,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.save_scheduled_web_delivery(UUID,UUID) TO everydayai;

RESET ROLE;

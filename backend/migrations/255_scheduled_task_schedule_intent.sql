-- 255: Persist future scheduling intent and fence each claimed run in existing tables.
-- Deploy with ALL schedulers/HTTP workers stopped and old in-flight runs drained.
-- Unknown historical running intent cannot be reconstructed safely: refuse migration.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
                   AND table_name='scheduled_tasks' AND column_name='schedule_enabled') THEN
        LOCK TABLE public.scheduled_tasks IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (SELECT 1 FROM scheduled_tasks WHERE status='running') THEN
            RAISE EXCEPTION 'SCHEDULED_TASK_UPGRADE_REQUIRES_DRAINED_RUNS';
        END IF;
        ALTER TABLE public.scheduled_tasks ADD COLUMN schedule_enabled BOOLEAN NOT NULL DEFAULT TRUE;
        UPDATE public.scheduled_tasks SET schedule_enabled=(status='active');
    END IF;
END $$;
ALTER TABLE public.scheduled_tasks
    ADD COLUMN IF NOT EXISTS run_token UUID,
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS run_claim_revision BIGINT,
    ADD COLUMN IF NOT EXISTS run_manual BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS run_previous_status TEXT;

-- Existing writers keep status semantics; running remains the exclusive claim.
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
        'run_previous_status','run_claim_revision','last_run_at','last_summary','last_result','run_count','consecutive_failures','updated_at'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['revision','status','next_run_at','run_token','claimed_at','run_manual',
        'run_previous_status','run_claim_revision','last_run_at','last_summary','last_result','run_count','consecutive_failures','updated_at']) THEN
        NEW.revision := OLD.revision+1;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS scheduled_tasks_intent_insert ON scheduled_tasks;
CREATE TRIGGER scheduled_tasks_intent_insert BEFORE INSERT ON scheduled_tasks
FOR EACH ROW EXECUTE FUNCTION public.bump_scheduled_task_revision();

CREATE OR REPLACE FUNCTION public.start_scheduled_task_run(p_task_id UUID,p_org_id UUID,p_run_token UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE v_task scheduled_tasks%ROWTYPE; v_inserted UUID;
BEGIN
    SELECT * INTO v_task FROM scheduled_tasks WHERE id=p_task_id AND org_id=p_org_id FOR UPDATE;
    IF NOT FOUND OR v_task.status <> 'running' OR v_task.run_token IS DISTINCT FROM p_run_token THEN
        RETURN jsonb_build_object('outcome','claim_lost');
    END IF;
    -- A repeated starter cannot revoke a run which crossed the start boundary
    -- before the user paused future scheduling.
    IF EXISTS (SELECT 1 FROM scheduled_task_runs WHERE id=p_run_token AND task_id=p_task_id AND org_id=p_org_id) THEN
        RETURN jsonb_build_object('outcome','already_started');
    END IF;
    IF NOT v_task.schedule_enabled AND (NOT v_task.run_manual OR v_task.revision <> v_task.run_claim_revision) THEN
        UPDATE scheduled_tasks SET status='paused',next_run_at=NULL,updated_at=NOW() WHERE id=p_task_id;
        RETURN jsonb_build_object('outcome','paused_before_start');
    END IF;
    INSERT INTO scheduled_task_runs(id,task_id,org_id,execution_id,plan_snapshot,status,started_at)
    VALUES(p_run_token,p_task_id,p_org_id,p_run_token,v_task.plan_snapshot,'running',NOW())
    ON CONFLICT(id) DO NOTHING RETURNING id INTO v_inserted;
    RETURN jsonb_build_object('outcome',CASE WHEN v_inserted IS NULL THEN 'already_started' ELSE 'started' END,
                             'run_id',v_inserted);
END $$;

CREATE OR REPLACE FUNCTION public.finish_scheduled_task_failure(
 p_task_id UUID,p_org_id UUID,p_run_id UUID,p_update JSONB,
 p_error TEXT,p_tokens INTEGER,p_duration INTEGER,p_stale_before TIMESTAMPTZ DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE v_task scheduled_tasks%ROWTYPE; v_status TEXT; v_next TIMESTAMPTZ;
BEGIN
    SELECT * INTO v_task FROM scheduled_tasks WHERE id=p_task_id AND org_id=p_org_id FOR UPDATE;
    IF NOT FOUND OR v_task.status <> 'running' OR v_task.run_token IS DISTINCT FROM p_run_id
       OR (p_stale_before IS NOT NULL AND v_task.claimed_at >= p_stale_before) THEN
        RETURN jsonb_build_object('outcome','claim_lost');
    END IF;
    IF p_stale_before IS NULL AND NOT EXISTS (
        SELECT 1 FROM scheduled_task_runs WHERE id=p_run_id AND task_id=p_task_id AND status='running'
    ) THEN
        RETURN jsonb_build_object('outcome','run_not_running');
    END IF;
    v_status := p_update->>'status';
    IF v_status NOT IN ('active','paused','error') OR v_status IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_FAILURE_STATUS_INVALID';
    END IF;
    v_next := NULLIF(p_update->>'next_run_at','')::timestamptz;
    IF NOT v_task.schedule_enabled THEN
        v_status := CASE WHEN v_task.run_previous_status='error' THEN 'error' ELSE 'paused' END;
    END IF;
    IF v_status <> 'active' THEN v_next:=NULL; END IF;
    UPDATE scheduled_task_runs SET status='failed',error_message=LEFT(p_error,500),tokens_used=p_tokens,
        duration_ms=p_duration,finished_at=NOW()
      WHERE id=p_run_id AND task_id=p_task_id AND org_id=p_org_id AND status='running';
    UPDATE scheduled_tasks SET status=v_status,next_run_at=v_next,
        consecutive_failures=COALESCE((p_update->>'consecutive_failures')::integer,consecutive_failures),updated_at=NOW()
      WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','finished','status',v_status,'next_run_at',v_next);
END $$;
REVOKE ALL ON FUNCTION public.start_scheduled_task_run(UUID,UUID,UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.finish_scheduled_task_failure(UUID,UUID,UUID,JSONB,TEXT,INTEGER,INTEGER,TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.start_scheduled_task_run(UUID,UUID,UUID) TO everydayai;
GRANT EXECUTE ON FUNCTION public.finish_scheduled_task_failure(UUID,UUID,UUID,JSONB,TEXT,INTEGER,INTEGER,TIMESTAMPTZ) TO everydayai;
CREATE OR REPLACE FUNCTION claim_due_tasks(p_now TIMESTAMPTZ, p_limit INT)
RETURNS SETOF scheduled_tasks AS $$
    UPDATE scheduled_tasks
    SET status = 'running',
        next_run_at = NULL  -- 清空，执行完再算下次
    WHERE id IN (
        SELECT id FROM scheduled_tasks
        WHERE status = 'active' AND schedule_enabled
          AND next_run_at IS NOT NULL
          AND next_run_at <= p_now
        ORDER BY next_run_at
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    RETURNING *;
$$ LANGUAGE sql;

CREATE OR REPLACE FUNCTION claim_scheduled_task_now(
    p_task_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task scheduled_tasks%ROWTYPE;
    v_previous_status TEXT;
BEGIN
    SELECT * INTO v_task FROM scheduled_tasks
     WHERE id = p_task_id AND org_id = p_org_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_task.status = 'running' THEN
        RETURN jsonb_build_object('outcome', 'already_running');
    END IF;

    v_previous_status := v_task.status;
    UPDATE scheduled_tasks
       SET status = 'running', run_manual = TRUE, next_run_at = NULL, updated_at = NOW()
     WHERE id = v_task.id
     RETURNING * INTO v_task;
    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'previous_status', v_previous_status,
        'task', to_jsonb(v_task)
    );
END;
$$;

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
            run_count, consecutive_failures, execution_policy, plan_snapshot, data_scope, revision
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
            COALESCE(p_definition->'data_scope', '{"kind":"task_prompt"}'::JSONB), 1
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
                   SET name = p_definition->>'name',
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
           OR v_delivery->'target_context'->>'type' NOT IN ('wecom_user', 'wecom_group')
           OR COALESCE(v_delivery->'target_context'->>'chatid', '') = '' THEN
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
       SET status = CASE WHEN v_task.status = 'running' THEN p_next_status ELSE v_task.status END,
           next_run_at = CASE
               WHEN v_task.status = 'running' THEN p_next_run_at
               ELSE v_task.next_run_at
           END,
           last_run_at = NOW(),
           last_summary = p_last_summary,
           last_result = p_last_result,
           run_count = v_task.run_count + 1,
           consecutive_failures = 0,
           updated_at = NOW()
     WHERE id = p_task_id;

    UPDATE scheduled_task_runs
       SET status = 'success',
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

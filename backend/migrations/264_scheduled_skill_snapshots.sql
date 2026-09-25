-- P2-4: reviewed revision pins and immutable scheduled configuration history.
-- Apply transactionally after draining scheduled workers; never backfill running claims.
DO $$ BEGIN
    LOCK TABLE public.scheduled_tasks IN ACCESS EXCLUSIVE MODE;
    IF EXISTS (SELECT 1 FROM public.scheduled_tasks WHERE status='running') THEN
        RAISE EXCEPTION 'SCHEDULED_SKILL_UPGRADE_REQUIRES_DRAIN';
    END IF;
END $$;

ALTER TABLE public.skill_revisions ADD COLUMN reviewed BOOLEAN NOT NULL DEFAULT FALSE;
-- A completed draft publication proves the approval/hash guard ran. Legacy
-- publication with no such evidence remains unreviewed (no inferred approval).
ALTER TABLE public.skill_revisions DISABLE TRIGGER skill_revisions_guard;
UPDATE public.skill_revisions r SET reviewed=TRUE WHERE EXISTS (
    SELECT 1 FROM public.skill_change_audits a WHERE a.package_id=r.package_id
    AND a.revision=r.revision AND a.entity='skill_drafts' AND a.to_state='published'
);
ALTER TABLE public.skill_revisions ENABLE TRIGGER skill_revisions_guard;
CREATE FUNCTION public.attest_skill_revision_review() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
    NEW.reviewed := EXISTS (SELECT 1 FROM public.skill_drafts d
        WHERE d.package_id=NEW.package_id AND d.revision=NEW.revision
        AND d.status='in_review' AND d.approved_by IS NOT NULL
        AND d.approved_sha256=NEW.content_sha256);
    RETURN NEW;
END $$;
CREATE TRIGGER skill_revision_review_attestation BEFORE INSERT ON public.skill_revisions
FOR EACH ROW EXECUTE FUNCTION public.attest_skill_revision_review();
REVOKE ALL ON FUNCTION public.attest_skill_revision_review() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.attest_skill_revision_review() TO everydayai;

ALTER TABLE public.scheduled_tasks
    ADD COLUMN skill_revision_snapshot JSONB NOT NULL DEFAULT '{"version":1,"skills":[]}',
    ADD COLUMN config_revision UUID,
    ADD COLUMN run_config_revision UUID,
    ADD COLUMN retry_config_revision UUID;
CREATE TABLE public.scheduled_task_config_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL,
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    definition_snapshot JSONB NOT NULL CHECK (jsonb_typeof(definition_snapshot)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(id,task_id,org_id)
);
CREATE INDEX scheduled_task_config_history ON public.scheduled_task_config_revisions(task_id,created_at);
ALTER TABLE public.scheduled_task_config_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_config_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY scheduled_config_service ON public.scheduled_task_config_revisions FOR ALL TO everydayai
USING (SESSION_USER='everydayai') WITH CHECK (SESSION_USER='everydayai');
REVOKE ALL ON public.scheduled_task_config_revisions FROM PUBLIC;
GRANT SELECT,INSERT ON public.scheduled_task_config_revisions TO everydayai;

CREATE FUNCTION public.scheduled_task_definition(p_task public.scheduled_tasks) RETURNS JSONB
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
    SELECT jsonb_object_agg(key,value) FROM jsonb_each(to_jsonb(p_task))
    WHERE key=ANY(ARRAY['id','org_id','user_id','name','prompt','cron_expr','schedule_type','weekdays',
        'day_of_month','run_at','timezone','push_target','template_file','max_credits','retry_count',
        'timeout_sec','execution_policy','plan_snapshot','data_scope','skill_revision_snapshot']);
$$;
INSERT INTO public.scheduled_task_config_revisions(task_id,org_id,definition_snapshot)
SELECT id,org_id,public.scheduled_task_definition(t) FROM public.scheduled_tasks t;
ALTER TABLE public.scheduled_tasks DISABLE TRIGGER scheduled_tasks_revision_bump;
UPDATE public.scheduled_tasks t SET config_revision=c.id FROM public.scheduled_task_config_revisions c
WHERE c.task_id=t.id AND c.org_id=t.org_id;
ALTER TABLE public.scheduled_tasks ENABLE TRIGGER scheduled_tasks_revision_bump;
ALTER TABLE public.scheduled_tasks ALTER COLUMN config_revision SET NOT NULL;
ALTER TABLE public.scheduled_tasks
    ADD FOREIGN KEY(config_revision,id,org_id) REFERENCES public.scheduled_task_config_revisions(id,task_id,org_id),
    ADD FOREIGN KEY(run_config_revision,id,org_id) REFERENCES public.scheduled_task_config_revisions(id,task_id,org_id),
    ADD FOREIGN KEY(retry_config_revision,id,org_id) REFERENCES public.scheduled_task_config_revisions(id,task_id,org_id);
ALTER TABLE public.scheduled_task_runs
    ADD COLUMN config_revision UUID,
    ADD COLUMN skill_revision_snapshot JSONB NOT NULL DEFAULT '{"version":1,"skills":[]}',
    ADD COLUMN definition_snapshot JSONB,
    ADD FOREIGN KEY(config_revision,task_id,org_id) REFERENCES public.scheduled_task_config_revisions(id,task_id,org_id);

CREATE FUNCTION public.guard_scheduled_config_history() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
    IF TG_OP <> 'INSERT' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'SCHEDULED_CONFIG_IMMUTABLE' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER scheduled_config_immutable BEFORE INSERT OR UPDATE OR DELETE
ON public.scheduled_task_config_revisions FOR EACH ROW EXECUTE FUNCTION public.guard_scheduled_config_history();

CREATE FUNCTION public.capture_scheduled_task_config() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE definition JSONB;
BEGIN
    definition := public.scheduled_task_definition(NEW);
    IF TG_OP='INSERT' OR definition IS DISTINCT FROM public.scheduled_task_definition(OLD) THEN
        IF jsonb_typeof(NEW.skill_revision_snapshot) IS DISTINCT FROM 'object'
           OR NEW.skill_revision_snapshot->'version' IS DISTINCT FROM '1'::jsonb
           OR jsonb_typeof(NEW.skill_revision_snapshot->'skills') IS DISTINCT FROM 'array'
           OR jsonb_array_length(NEW.skill_revision_snapshot->'skills') > 4 THEN
            RAISE EXCEPTION 'SKILL_SCHEDULED_SNAPSHOT_INVALID' USING ERRCODE='23514';
        END IF;
        INSERT INTO public.scheduled_task_config_revisions(task_id,org_id,definition_snapshot)
        VALUES(NEW.id,NEW.org_id,definition) RETURNING id INTO NEW.config_revision;
    ELSE
        NEW.config_revision := OLD.config_revision;
    END IF;
    IF TG_OP='UPDATE' AND OLD.status <> 'running' AND NEW.status='running' THEN
        NEW.run_config_revision := COALESCE(OLD.retry_config_revision,OLD.config_revision);
    ELSIF NEW.status <> 'running' THEN
        NEW.run_config_revision := NULL;
    ELSE
        NEW.run_config_revision := OLD.run_config_revision;
    END IF;
    RETURN NEW;
END $$;
-- After the existing lifecycle trigger: bookkeeping and definition identity stay separate.
CREATE TRIGGER zz_scheduled_task_config BEFORE INSERT OR UPDATE ON public.scheduled_tasks
FOR EACH ROW EXECUTE FUNCTION public.capture_scheduled_task_config();

CREATE FUNCTION public.guard_scheduled_run_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
    IF (NEW.config_revision,NEW.skill_revision_snapshot,NEW.definition_snapshot)
        IS DISTINCT FROM (OLD.config_revision,OLD.skill_revision_snapshot,OLD.definition_snapshot) THEN
        RAISE EXCEPTION 'SCHEDULED_RUN_SNAPSHOT_IMMUTABLE' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER scheduled_run_snapshot_immutable BEFORE UPDATE ON public.scheduled_task_runs
FOR EACH ROW EXECUTE FUNCTION public.guard_scheduled_run_snapshot();
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
        'config_revision','run_config_revision','retry_config_revision','run_previous_status','run_claim_revision','last_run_at','last_summary','last_result','run_count','consecutive_failures','updated_at'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['revision','status','next_run_at','run_token','claimed_at','run_manual',
        'config_revision','run_config_revision','retry_config_revision','run_previous_status','run_claim_revision','last_run_at','last_summary','last_result','run_count','consecutive_failures','updated_at']) THEN
        NEW.revision := OLD.revision+1;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.start_scheduled_task_run(p_task_id UUID,p_org_id UUID,p_run_token UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE v_task scheduled_tasks%ROWTYPE; v_inserted UUID; v_definition JSONB;
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
    SELECT definition_snapshot INTO STRICT v_definition FROM public.scheduled_task_config_revisions
    WHERE id=v_task.run_config_revision AND task_id=p_task_id AND org_id=p_org_id;
    INSERT INTO scheduled_task_runs(id,task_id,org_id,execution_id,plan_snapshot,status,started_at,
        config_revision,skill_revision_snapshot,definition_snapshot)
    VALUES(p_run_token,p_task_id,p_org_id,p_run_token,v_definition->'plan_snapshot','running',NOW(),
        v_task.run_config_revision,v_definition->'skill_revision_snapshot',v_definition)
    ON CONFLICT(id) DO NOTHING RETURNING id INTO v_inserted;
    RETURN jsonb_build_object('outcome',CASE WHEN v_inserted IS NULL THEN 'already_started' ELSE 'started' END,
                             'run_id',v_inserted,'config_revision',v_task.run_config_revision,'definition_snapshot',v_definition);
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
        retry_config_revision=CASE WHEN v_status='active' AND
            (COALESCE((p_update->>'retry_scheduled')::boolean,FALSE) OR p_stale_before IS NOT NULL)
            THEN v_task.run_config_revision ELSE NULL END,
        consecutive_failures=COALESCE((p_update->>'consecutive_failures')::integer,consecutive_failures),updated_at=NOW()
      WHERE id=p_task_id;
    RETURN jsonb_build_object('outcome','finished','status',v_status,'next_run_at',v_next);
END $$;

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
       SET retry_config_revision = NULL, status = CASE WHEN v_task.status = 'running' THEN p_next_status ELSE v_task.status END,
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
            run_count, consecutive_failures, execution_policy, plan_snapshot, data_scope, revision, skill_revision_snapshot
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
            COALESCE(p_definition->'skill_revision_snapshot','{"version":1,"skills":[]}'::jsonb)
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

CREATE OR REPLACE FUNCTION public.confirm_scheduled_task_draft(
    p_draft_id UUID,
    p_org_id UUID,
    p_user_id UUID,
    p_config_hash TEXT,
    p_task_id UUID,
    p_next_run_at TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_draft scheduled_task_drafts%ROWTYPE;
    v_definition JSONB;
    v_source scheduled_tasks%ROWTYPE;
    v_source_status TEXT;
BEGIN
    SELECT * INTO v_draft FROM scheduled_task_drafts
      WHERE id = p_draft_id AND org_id = p_org_id AND user_id = p_user_id
      FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;
    IF v_draft.status = 'confirmed' THEN
        RETURN jsonb_build_object(
            'outcome', 'confirmed',
            'task_id', COALESCE(v_draft.confirmed_task_id, v_draft.source_task_id)
        );
    END IF;
    IF v_draft.status <> 'ready' OR v_draft.config_hash <> p_config_hash
       OR v_draft.preflight_config_hash <> p_config_hash OR v_draft.expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'not_ready');
    END IF;

    v_definition := v_draft.definition;
    IF v_draft.source_task_id IS NULL THEN
        INSERT INTO scheduled_tasks(
            id, org_id, user_id, name, prompt, cron_expr, schedule_type, weekdays,
            day_of_month, run_at, timezone, push_target, template_file, status,
            max_credits, retry_count, timeout_sec, next_run_at, run_count,
            consecutive_failures, execution_policy, plan_snapshot, skill_revision_snapshot
        ) VALUES (
            p_task_id, p_org_id, p_user_id, v_definition->>'name', v_definition->>'prompt',
            NULLIF(v_definition->>'cron_expr',''), v_definition->>'schedule_type',
            CASE WHEN jsonb_typeof(v_definition->'weekdays') = 'array' THEN ARRAY(SELECT jsonb_array_elements_text(v_definition->'weekdays')::smallint) END,
            NULLIF(v_definition->>'day_of_month','')::smallint,
            NULLIF(v_definition->>'run_at','')::timestamptz, COALESCE(v_definition->>'timezone','Asia/Shanghai'),
            v_definition->'push_target', v_definition->'template_file', 'active',
            COALESCE((v_definition->>'max_credits')::integer, 10),
            COALESCE((v_definition->>'retry_count')::smallint, 1),
            COALESCE((v_definition->>'timeout_sec')::integer, 180), p_next_run_at,
            0, 0, v_draft.execution_policy, v_draft.plan,
            COALESCE(v_definition->'skill_revision_snapshot','{"version":1,"skills":[]}'::jsonb)
        );
        UPDATE scheduled_task_drafts
           SET status = 'confirmed', confirmed_task_id = p_task_id, updated_at = NOW()
         WHERE id = p_draft_id;
        RETURN jsonb_build_object('outcome', 'created', 'task_id', p_task_id);
    END IF;

    SELECT * INTO v_source FROM scheduled_tasks
      WHERE id = v_draft.source_task_id AND org_id = p_org_id
      FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'source_missing');
    END IF;
    IF v_source.status = 'running' THEN
        RETURN jsonb_build_object('outcome', 'source_running');
    END IF;

    v_source_status := CASE
        WHEN v_source.status = 'active' THEN 'active'
        ELSE 'paused'
    END;
    UPDATE scheduled_tasks
       SET name = v_definition->>'name',
           prompt = v_definition->>'prompt',
           cron_expr = NULLIF(v_definition->>'cron_expr',''),
           schedule_type = v_definition->>'schedule_type',
           weekdays = CASE WHEN jsonb_typeof(v_definition->'weekdays') = 'array' THEN ARRAY(SELECT jsonb_array_elements_text(v_definition->'weekdays')::smallint) END,
           day_of_month = NULLIF(v_definition->>'day_of_month','')::smallint,
           run_at = NULLIF(v_definition->>'run_at','')::timestamptz,
           timezone = COALESCE(v_definition->>'timezone','Asia/Shanghai'),
           push_target = v_definition->'push_target',
           template_file = v_definition->'template_file',
           max_credits = COALESCE((v_definition->>'max_credits')::integer, 10),
           retry_count = COALESCE((v_definition->>'retry_count')::smallint, 1),
           timeout_sec = COALESCE((v_definition->>'timeout_sec')::integer, 180),
           status = v_source_status,
           next_run_at = CASE WHEN v_source_status = 'active' THEN p_next_run_at ELSE NULL END,
           consecutive_failures = 0,
           execution_policy = v_draft.execution_policy,
           plan_snapshot = v_draft.plan,
           skill_revision_snapshot = COALESCE(v_definition->'skill_revision_snapshot','{"version":1,"skills":[]}'::jsonb),
           updated_at = NOW()
     WHERE id = v_source.id;
    UPDATE scheduled_task_drafts
       SET status = 'confirmed', confirmed_task_id = v_source.id, updated_at = NOW()
     WHERE id = p_draft_id;
    RETURN jsonb_build_object('outcome', 'updated', 'task_id', v_source.id);
END;
$$;
REVOKE ALL ON FUNCTION public.scheduled_task_definition(public.scheduled_tasks),
 public.guard_scheduled_config_history(),public.capture_scheduled_task_config(),public.guard_scheduled_run_snapshot() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.scheduled_task_definition(public.scheduled_tasks),
 public.guard_scheduled_config_history(),public.capture_scheduled_task_config(),public.guard_scheduled_run_snapshot() TO everydayai;

-- 244: 定时任务受控工作流。活跃任务与创建草稿分离，预检成功后才允许启用。

ALTER TABLE public.scheduled_tasks
    ADD COLUMN IF NOT EXISTS execution_policy JSONB,
    ADD COLUMN IF NOT EXISTS plan_snapshot JSONB;

ALTER TABLE public.scheduled_task_runs
    ADD COLUMN IF NOT EXISTS execution_id UUID,
    ADD COLUMN IF NOT EXISTS completion_gate JSONB,
    ADD COLUMN IF NOT EXISTS plan_snapshot JSONB;

CREATE TABLE IF NOT EXISTS public.scheduled_task_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    definition JSONB NOT NULL,
    config_hash TEXT NOT NULL,
    plan JSONB,
    execution_policy JSONB,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','planning','preflight_running','ready','failed','confirmed','expired')),
    latest_preflight_id UUID,
    preflight_config_hash TEXT,
    confirmed_task_id UUID REFERENCES public.scheduled_tasks(id),
    error_message TEXT,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_task_drafts_owner
    ON public.scheduled_task_drafts(org_id, user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.scheduled_task_preflight_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id UUID NOT NULL REFERENCES public.scheduled_task_drafts(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    config_hash TEXT NOT NULL,
    definition_snapshot JSONB NOT NULL,
    plan_snapshot JSONB NOT NULL,
    policy_snapshot JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','passed','failed','timeout')),
    result_summary TEXT,
    error_message TEXT,
    completion_gate JSONB,
    tool_trace JSONB NOT NULL DEFAULT '[]'::JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_task_preflight_draft
    ON public.scheduled_task_preflight_runs(draft_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.scheduled_task_execution_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    execution_kind TEXT NOT NULL CHECK (execution_kind IN ('preflight','run')),
    execution_id UUID NOT NULL,
    task_id UUID REFERENCES public.scheduled_tasks(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    status TEXT NOT NULL,
    elapsed_ms INTEGER,
    summary TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_task_execution_events
    ON public.scheduled_task_execution_events(execution_kind, execution_id, step_order);

ALTER TABLE public.scheduled_task_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_drafts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_preflight_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_preflight_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_execution_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scheduled_task_execution_events FORCE ROW LEVEL SECURITY;

CREATE POLICY scheduled_task_drafts_legacy_service ON public.scheduled_task_drafts
FOR ALL TO everydayai USING (SESSION_USER = 'everydayai') WITH CHECK (SESSION_USER = 'everydayai');
CREATE POLICY scheduled_task_preflight_runs_legacy_service ON public.scheduled_task_preflight_runs
FOR ALL TO everydayai USING (SESSION_USER = 'everydayai') WITH CHECK (SESSION_USER = 'everydayai');
CREATE POLICY scheduled_task_execution_events_legacy_service ON public.scheduled_task_execution_events
FOR ALL TO everydayai USING (SESSION_USER = 'everydayai') WITH CHECK (SESSION_USER = 'everydayai');

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
BEGIN
    SELECT * INTO v_draft FROM scheduled_task_drafts
      WHERE id = p_draft_id AND org_id = p_org_id AND user_id = p_user_id
      FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;
    IF v_draft.status = 'confirmed' THEN
        RETURN jsonb_build_object('outcome', 'confirmed', 'task_id', v_draft.confirmed_task_id);
    END IF;
    IF v_draft.status <> 'ready' OR v_draft.config_hash <> p_config_hash
       OR v_draft.preflight_config_hash <> p_config_hash OR v_draft.expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'not_ready');
    END IF;
    v_definition := v_draft.definition;
    INSERT INTO scheduled_tasks(
        id, org_id, user_id, name, prompt, cron_expr, schedule_type, weekdays,
        day_of_month, run_at, timezone, push_target, template_file, status,
        max_credits, retry_count, timeout_sec, next_run_at, run_count,
        consecutive_failures, execution_policy, plan_snapshot
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
        0, 0, v_draft.execution_policy, v_draft.plan
    );
    UPDATE scheduled_task_drafts
       SET status = 'confirmed', confirmed_task_id = p_task_id, updated_at = NOW()
     WHERE id = p_draft_id;
    RETURN jsonb_build_object('outcome', 'created', 'task_id', p_task_id);
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_scheduled_task_draft(UUID, UUID, UUID, TEXT, UUID, TIMESTAMPTZ) FROM PUBLIC;

-- 248: AI ChangeSet 通用变更事务内核。
--
-- 业务表仍由各自适配器维护。本迁移只建立变更交易、检查节点和可恢复事件
-- 时间线；通用 RPC 不接收表名或 SQL，不可能通过 proposed_snapshot 任意写业务表。

CREATE TABLE IF NOT EXISTS public.change_sets (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    resource_type         TEXT NOT NULL,
    resource_id           TEXT NOT NULL,
    operation             TEXT NOT NULL,
    base_revision         TEXT NOT NULL,
    base_snapshot         JSONB NOT NULL DEFAULT '{}'::JSONB,
    proposed_snapshot     JSONB NOT NULL DEFAULT '{}'::JSONB,
    patch                 JSONB NOT NULL DEFAULT '[]'::JSONB,
    diff                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    risk_level            TEXT NOT NULL DEFAULT 'medium'
        CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    policy_snapshot       JSONB NOT NULL DEFAULT '{}'::JSONB,
    plan_snapshot         JSONB,
    tool_policy_snapshot  JSONB,
    check_summary         JSONB,
    status                TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft', 'resolving', 'proposed', 'validating', 'preflighting',
            'awaiting_approval', 'committing', 'applied', 'cancelled',
            'rejected', 'failed', 'expired', 'conflicted'
        )),
    idempotency_key       TEXT NOT NULL,
    expires_at            TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours',
    created_by            TEXT NOT NULL,
    created_by_type       TEXT NOT NULL DEFAULT 'user'
        CHECK (created_by_type IN ('user', 'ai', 'system', 'service')),
    updated_by            TEXT,
    updated_by_type       TEXT,
    audit_subject         JSONB NOT NULL DEFAULT '{}'::JSONB,
    recovery_of_id        UUID REFERENCES public.change_sets(id) ON DELETE SET NULL,
    committed_revision    TEXT,
    error_code            TEXT,
    error_message         TEXT,
    conflict              JSONB,
    revision              BIGINT NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT change_sets_org_id_unique UNIQUE (org_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_change_sets_org_idempotency
    ON public.change_sets(org_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_change_sets_org_status
    ON public.change_sets(org_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_change_sets_resource
    ON public.change_sets(org_id, resource_type, resource_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.change_checks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    change_set_id   UUID NOT NULL,
    org_id          UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    CONSTRAINT change_checks_set_fk
        FOREIGN KEY (org_id, change_set_id)
        REFERENCES public.change_sets(org_id, id) ON DELETE CASCADE,
    check_type      TEXT NOT NULL
        CHECK (check_type IN ('validation', 'preflight', 'authorization', 'approval', 'conflict', 'commit', 'restore')),
    check_key       TEXT NOT NULL,
    input           JSONB NOT NULL DEFAULT '{}'::JSONB,
    result          JSONB NOT NULL DEFAULT '{}'::JSONB,
    status          TEXT NOT NULL
        CHECK (status IN ('pending', 'running', 'passed', 'failed', 'skipped')),
    actor_id        TEXT,
    actor_type      TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_change_checks_set_time
    ON public.change_checks(change_set_id, created_at);
CREATE INDEX IF NOT EXISTS idx_change_checks_org_time
    ON public.change_checks(org_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.change_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    change_set_id   UUID NOT NULL,
    org_id          UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    CONSTRAINT change_events_set_fk
        FOREIGN KEY (org_id, change_set_id)
        REFERENCES public.change_sets(org_id, id) ON DELETE CASCADE,
    sequence        BIGINT NOT NULL,
    event_type      TEXT NOT NULL,
    from_status     TEXT,
    to_status       TEXT,
    actor_id        TEXT,
    actor_type      TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(change_set_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_change_events_set_sequence
    ON public.change_events(change_set_id, sequence);
CREATE INDEX IF NOT EXISTS idx_change_events_org_time
    ON public.change_events(org_id, created_at DESC);

ALTER TABLE public.change_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.change_sets FORCE ROW LEVEL SECURITY;
ALTER TABLE public.change_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.change_checks FORCE ROW LEVEL SECURITY;
ALTER TABLE public.change_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.change_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS change_sets_legacy_service ON public.change_sets;
DROP POLICY IF EXISTS change_checks_legacy_service ON public.change_checks;
DROP POLICY IF EXISTS change_events_legacy_service ON public.change_events;

CREATE POLICY change_sets_legacy_service ON public.change_sets
FOR ALL TO everydayai USING (SESSION_USER = 'everydayai') WITH CHECK (SESSION_USER = 'everydayai');
CREATE POLICY change_checks_legacy_service ON public.change_checks
FOR ALL TO everydayai USING (SESSION_USER = 'everydayai') WITH CHECK (SESSION_USER = 'everydayai');
CREATE POLICY change_events_legacy_service ON public.change_events
FOR ALL TO everydayai USING (SESSION_USER = 'everydayai') WITH CHECK (SESSION_USER = 'everydayai');

CREATE OR REPLACE FUNCTION public.create_change_set(
    p_id UUID,
    p_org_id UUID,
    p_resource_type TEXT,
    p_resource_id TEXT,
    p_operation TEXT,
    p_base_revision TEXT,
    p_base_snapshot JSONB,
    p_proposed_snapshot JSONB,
    p_patch JSONB,
    p_diff JSONB,
    p_risk_level TEXT,
    p_policy_snapshot JSONB,
    p_plan_snapshot JSONB,
    p_tool_policy_snapshot JSONB,
    p_check_summary JSONB,
    p_idempotency_key TEXT,
    p_expires_at TIMESTAMPTZ,
    p_actor_id TEXT,
    p_actor_type TEXT,
    p_audit_subject JSONB,
    p_recovery_of_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_existing public.change_sets%ROWTYPE;
    v_created public.change_sets%ROWTYPE;
BEGIN
    SELECT * INTO v_existing
      FROM public.change_sets
     WHERE org_id = p_org_id AND idempotency_key = p_idempotency_key
     FOR UPDATE;
    IF FOUND THEN
        IF v_existing.resource_type <> p_resource_type
           OR v_existing.resource_id <> p_resource_id
           OR v_existing.operation <> p_operation
           OR v_existing.base_revision <> p_base_revision
           OR v_existing.base_snapshot IS DISTINCT FROM COALESCE(p_base_snapshot, '{}'::JSONB)
           OR v_existing.proposed_snapshot IS DISTINCT FROM COALESCE(p_proposed_snapshot, '{}'::JSONB) THEN
            RETURN jsonb_build_object('outcome', 'idempotency_conflict', 'change_set', to_jsonb(v_existing));
        END IF;
        RETURN jsonb_build_object('outcome', 'existing', 'change_set', to_jsonb(v_existing));
    END IF;

    INSERT INTO public.change_sets(
        id, org_id, resource_type, resource_id, operation, base_revision,
        base_snapshot, proposed_snapshot, patch, diff, risk_level, policy_snapshot,
        plan_snapshot, tool_policy_snapshot, check_summary, idempotency_key,
        expires_at, created_by, created_by_type, audit_subject, recovery_of_id
    ) VALUES (
        p_id, p_org_id, p_resource_type, p_resource_id, p_operation, p_base_revision,
        COALESCE(p_base_snapshot, '{}'::JSONB), COALESCE(p_proposed_snapshot, '{}'::JSONB),
        COALESCE(p_patch, '[]'::JSONB), COALESCE(p_diff, '{}'::JSONB),
        COALESCE(p_risk_level, 'medium'), COALESCE(p_policy_snapshot, '{}'::JSONB),
        p_plan_snapshot, p_tool_policy_snapshot, p_check_summary, p_idempotency_key,
        COALESCE(p_expires_at, NOW() + INTERVAL '24 hours'), p_actor_id, p_actor_type,
        COALESCE(p_audit_subject, '{}'::JSONB), p_recovery_of_id
    ) ON CONFLICT (org_id, idempotency_key) DO NOTHING
      RETURNING * INTO v_created;

    -- 并发创建时，唯一键冲突不会向调用方泄漏为 500；重新锁定赢家并按
    -- 同一候选内容比较，保证“重复请求回放、不同请求拒绝”的幂等语义。
    IF NOT FOUND THEN
        SELECT * INTO v_existing
          FROM public.change_sets
         WHERE org_id = p_org_id AND idempotency_key = p_idempotency_key
         FOR UPDATE;
        IF v_existing.resource_type <> p_resource_type
           OR v_existing.resource_id <> p_resource_id
           OR v_existing.operation <> p_operation
           OR v_existing.base_revision <> p_base_revision
           OR v_existing.base_snapshot IS DISTINCT FROM COALESCE(p_base_snapshot, '{}'::JSONB)
           OR v_existing.proposed_snapshot IS DISTINCT FROM COALESCE(p_proposed_snapshot, '{}'::JSONB) THEN
            RETURN jsonb_build_object('outcome', 'idempotency_conflict', 'change_set', to_jsonb(v_existing));
        END IF;
        RETURN jsonb_build_object('outcome', 'existing', 'change_set', to_jsonb(v_existing));
    END IF;

    INSERT INTO public.change_events(
        change_set_id, org_id, sequence, event_type, to_status,
        actor_id, actor_type, payload
    ) VALUES (
        v_created.id, v_created.org_id, 1, 'created', v_created.status,
        p_actor_id, p_actor_type,
        jsonb_build_object('contract_version', 'changeset.v1')
    );
    RETURN jsonb_build_object('outcome', 'created', 'change_set', to_jsonb(v_created));
END;
$$;

CREATE OR REPLACE FUNCTION public.transition_change_set(
    p_change_set_id UUID,
    p_org_id UUID,
    p_expected_status TEXT,
    p_next_status TEXT,
    p_actor_id TEXT,
    p_actor_type TEXT,
    p_event_type TEXT,
    p_payload JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_row public.change_sets%ROWTYPE;
    v_from_status TEXT;
    v_sequence BIGINT;
BEGIN
    SELECT * INTO v_row
      FROM public.change_sets
     WHERE id = p_change_set_id AND org_id = p_org_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;

    IF v_row.status <> p_expected_status THEN
        RETURN jsonb_build_object('outcome', 'state_conflict', 'change_set', to_jsonb(v_row));
    END IF;

    -- 到期优先于后续人工操作；committing 由恢复器处理，避免业务提交中途被过期抢占。
    IF v_row.expires_at <= NOW()
       AND v_row.status NOT IN ('applied', 'cancelled', 'rejected', 'failed', 'expired', 'conflicted', 'committing')
       AND p_next_status <> 'expired' THEN
        v_from_status := v_row.status;
        UPDATE public.change_sets
           SET status = 'expired', updated_by = p_actor_id, updated_by_type = p_actor_type,
               revision = revision + 1, updated_at = NOW(),
               audit_subject = audit_subject || jsonb_build_object('last_actor_id', p_actor_id, 'last_actor_type', p_actor_type)
         WHERE id = v_row.id
         RETURNING * INTO v_row;
        SELECT COALESCE(MAX(sequence), 0) + 1 INTO v_sequence
          FROM public.change_events WHERE change_set_id = v_row.id;
        INSERT INTO public.change_events(
            change_set_id, org_id, sequence, event_type, from_status, to_status,
            actor_id, actor_type, payload
        ) VALUES (
            v_row.id, v_row.org_id, v_sequence, 'expired', v_from_status, 'expired',
            p_actor_id, p_actor_type, '{}'::JSONB
        );
        RETURN jsonb_build_object('outcome', 'expired', 'change_set', to_jsonb(v_row));
    END IF;

    IF NOT (
        (v_row.status = 'draft' AND p_next_status IN ('resolving', 'cancelled', 'expired')) OR
        (v_row.status = 'resolving' AND p_next_status IN ('proposed', 'failed', 'cancelled', 'expired')) OR
        (v_row.status = 'proposed' AND p_next_status IN ('validating', 'rejected', 'failed', 'cancelled', 'expired')) OR
        (v_row.status = 'validating' AND p_next_status IN ('preflighting', 'rejected', 'failed', 'cancelled', 'expired', 'conflicted')) OR
        (v_row.status = 'preflighting' AND p_next_status IN ('awaiting_approval', 'rejected', 'failed', 'cancelled', 'expired', 'conflicted')) OR
        (v_row.status = 'awaiting_approval' AND p_next_status IN ('committing', 'rejected', 'failed', 'cancelled', 'expired', 'conflicted')) OR
        (v_row.status = 'committing' AND p_next_status IN ('applied', 'failed', 'conflicted'))
    ) THEN
        RETURN jsonb_build_object('outcome', 'invalid_transition', 'change_set', to_jsonb(v_row));
    END IF;

    v_from_status := v_row.status;
    UPDATE public.change_sets
       SET status = p_next_status,
           updated_by = p_actor_id,
           updated_by_type = p_actor_type,
           committed_revision = CASE WHEN p_next_status = 'applied' THEN p_payload->>'new_revision' ELSE committed_revision END,
           error_code = CASE WHEN p_next_status = 'failed' THEN COALESCE(p_payload->>'error_type', 'changeset_failed') ELSE error_code END,
           error_message = CASE WHEN p_next_status = 'failed' THEN p_payload->>'error_message' ELSE error_message END,
           conflict = CASE WHEN p_next_status = 'conflicted' THEN p_payload ELSE conflict END,
           audit_subject = audit_subject || jsonb_build_object('last_actor_id', p_actor_id, 'last_actor_type', p_actor_type),
           revision = revision + 1,
           updated_at = NOW()
     WHERE id = v_row.id
     RETURNING * INTO v_row;
    SELECT COALESCE(MAX(sequence), 0) + 1 INTO v_sequence
      FROM public.change_events WHERE change_set_id = v_row.id;
    INSERT INTO public.change_events(
        change_set_id, org_id, sequence, event_type, from_status, to_status,
        actor_id, actor_type, payload
    ) VALUES (
        v_row.id, v_row.org_id, v_sequence, p_event_type, v_from_status, p_next_status,
        p_actor_id, p_actor_type, COALESCE(p_payload, '{}'::JSONB)
    );
    RETURN jsonb_build_object('outcome', 'transitioned', 'change_set', to_jsonb(v_row));
END;
$$;

CREATE OR REPLACE FUNCTION public.record_change_check(
    p_check_id UUID,
    p_change_set_id UUID,
    p_org_id UUID,
    p_check_type TEXT,
    p_check_key TEXT,
    p_status TEXT,
    p_input JSONB,
    p_result JSONB,
    p_actor_id TEXT,
    p_actor_type TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_check public.change_checks%ROWTYPE;
    v_set public.change_sets%ROWTYPE;
    v_sequence BIGINT;
BEGIN
    SELECT * INTO v_set
      FROM public.change_sets
     WHERE id = p_change_set_id AND org_id = p_org_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'missing');
    END IF;

    INSERT INTO public.change_checks(
        id, change_set_id, org_id, check_type, check_key, input, result,
        status, actor_id, actor_type, finished_at
    ) VALUES (
        p_check_id, p_change_set_id, p_org_id, p_check_type, p_check_key,
        COALESCE(p_input, '{}'::JSONB), COALESCE(p_result, '{}'::JSONB),
        p_status, p_actor_id, p_actor_type,
        CASE WHEN p_status IN ('passed', 'failed', 'skipped') THEN NOW() ELSE NULL END
    ) RETURNING * INTO v_check;

    SELECT COALESCE(MAX(sequence), 0) + 1 INTO v_sequence
      FROM public.change_events WHERE change_set_id = p_change_set_id;
    INSERT INTO public.change_events(
        change_set_id, org_id, sequence, event_type, actor_id, actor_type, payload
    ) VALUES (
        p_change_set_id, p_org_id, v_sequence, 'check.' || p_check_type,
        p_actor_id, p_actor_type,
        jsonb_build_object('check_id', v_check.id, 'check_key', p_check_key, 'status', p_status, 'result', COALESCE(p_result, '{}'::JSONB))
    );
    RETURN jsonb_build_object('outcome', 'recorded', 'check', to_jsonb(v_check));
END;
$$;

REVOKE ALL ON FUNCTION public.create_change_set(UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, JSONB, JSONB, JSONB, TEXT, JSONB, JSONB, JSONB, JSONB, TEXT, TIMESTAMPTZ, TEXT, TEXT, JSONB, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.transition_change_set(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_change_check(UUID, UUID, UUID, TEXT, TEXT, TEXT, JSONB, JSONB, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_change_set(UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, JSONB, JSONB, JSONB, TEXT, JSONB, JSONB, JSONB, JSONB, TEXT, TIMESTAMPTZ, TEXT, TEXT, JSONB, UUID) TO everydayai;
GRANT EXECUTE ON FUNCTION public.transition_change_set(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO everydayai;
GRANT EXECUTE ON FUNCTION public.record_change_check(UUID, UUID, UUID, TEXT, TEXT, TEXT, JSONB, JSONB, TEXT, TEXT) TO everydayai;

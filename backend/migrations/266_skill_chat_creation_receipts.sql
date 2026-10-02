-- Chat confirmation and Skill draft creation share an idempotent receipt.
CREATE TABLE public.skill_authoring_receipts (
    change_set_id UUID PRIMARY KEY,
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    actor_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    package_id UUID NOT NULL REFERENCES public.skill_packages(id) ON DELETE RESTRICT,
    operation TEXT NOT NULL CHECK (operation IN ('create', 'update')),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    draft_revision TEXT NOT NULL,
    draft_version BIGINT NOT NULL CHECK (draft_version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT skill_authoring_receipt_change_set_fk
        FOREIGN KEY (org_id, change_set_id)
        REFERENCES public.change_sets(org_id, id) ON DELETE RESTRICT
);
CREATE INDEX skill_authoring_receipts_package_time
    ON public.skill_authoring_receipts(org_id, package_id, created_at DESC);
ALTER TABLE public.skill_authoring_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_authoring_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY skill_authoring_receipt_admin ON public.skill_authoring_receipts FOR ALL TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin'
        AND skill_authoring_receipts.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()
        AND skill_authoring_receipts.actor_user_id IS NOT DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::uuid
        AND EXISTS (SELECT 1 FROM public.org_members m
            WHERE m.org_id = skill_authoring_receipts.org_id
              AND m.user_id = skill_authoring_receipts.actor_user_id
              AND m.status = 'active' AND m.role IN ('owner', 'admin')))
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin'
        AND skill_authoring_receipts.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()
        AND skill_authoring_receipts.actor_user_id IS NOT DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::uuid
        AND EXISTS (SELECT 1 FROM public.org_members m
            WHERE m.org_id = skill_authoring_receipts.org_id
              AND m.user_id = skill_authoring_receipts.actor_user_id
              AND m.status = 'active' AND m.role IN ('owner', 'admin')));
REVOKE ALL ON public.skill_authoring_receipts FROM PUBLIC;
GRANT SELECT, INSERT ON public.skill_authoring_receipts TO everydayai;

-- Trial inputs/results are scoped to the actor who ran the explicit preview.
-- Only hashes and the bounded result needed for idempotent UI recovery are kept.
CREATE TABLE public.skill_draft_trial_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    change_set_id UUID NOT NULL,
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    actor_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    idempotency_key UUID NOT NULL,
    candidate_revision BIGINT NOT NULL CHECK (candidate_revision >= 0),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    mode TEXT NOT NULL CHECK (mode IN ('text', 'image')),
    input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[a-f0-9]{64}$'),
    model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    result JSONB NOT NULL DEFAULT '{}'::JSONB,
    feedback_rating TEXT CHECK (feedback_rating IN ('helpful', 'not_helpful')),
    feedback_text TEXT NOT NULL DEFAULT '' CHECK (char_length(feedback_text) <= 1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    UNIQUE (org_id, change_set_id, actor_user_id, idempotency_key),
    CONSTRAINT skill_draft_trial_change_set_fk
        FOREIGN KEY (org_id, change_set_id)
        REFERENCES public.change_sets(org_id, id) ON DELETE RESTRICT
);
CREATE INDEX skill_draft_trial_actor_time
    ON public.skill_draft_trial_runs(org_id, actor_user_id, created_at DESC);
ALTER TABLE public.skill_draft_trial_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_draft_trial_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY skill_draft_trial_actor ON public.skill_draft_trial_runs FOR ALL TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin'
        AND skill_draft_trial_runs.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()
        AND skill_draft_trial_runs.actor_user_id IS NOT DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::uuid
        AND EXISTS (SELECT 1 FROM public.org_members m
            WHERE m.org_id = skill_draft_trial_runs.org_id
              AND m.user_id = skill_draft_trial_runs.actor_user_id
              AND m.status = 'active' AND m.role IN ('owner', 'admin')))
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin'
        AND skill_draft_trial_runs.org_id IS NOT DISTINCT FROM public.skill_catalog_org_id()
        AND skill_draft_trial_runs.actor_user_id IS NOT DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::uuid
        AND EXISTS (SELECT 1 FROM public.org_members m
            WHERE m.org_id = skill_draft_trial_runs.org_id
              AND m.user_id = skill_draft_trial_runs.actor_user_id
              AND m.status = 'active' AND m.role IN ('owner', 'admin')));
REVOKE ALL ON public.skill_draft_trial_runs FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON public.skill_draft_trial_runs TO everydayai;

-- A user edit creates a new frozen version of the same pending proposal.
CREATE FUNCTION public.replace_skill_draft_candidate(
    p_change_set_id UUID, p_org_id UUID, p_actor_id UUID,
    p_expected_revision BIGINT, p_proposed_snapshot JSONB, p_content_sha256 TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_row public.change_sets%ROWTYPE; v_sequence BIGINT;
BEGIN
    IF p_actor_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::uuid
       OR p_org_id IS DISTINCT FROM public.skill_catalog_org_id()
       OR p_content_sha256 !~ '^[a-f0-9]{64}$'
       OR p_proposed_snapshot->>'content_sha256' IS DISTINCT FROM p_content_sha256 THEN
        RAISE EXCEPTION 'SKILL_CANDIDATE_REPLACE_DENIED' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_row FROM public.change_sets
     WHERE id = p_change_set_id AND org_id = p_org_id FOR UPDATE;
    IF NOT FOUND OR v_row.resource_type <> 'skill_draft' OR v_row.status <> 'awaiting_approval'
       OR v_row.expires_at <= now()
       OR v_row.created_by <> p_actor_id::text OR v_row.revision <> p_expected_revision
       OR NOT EXISTS (SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
           JOIN public.users u ON u.id=m.user_id WHERE m.org_id=p_org_id AND m.user_id=p_actor_id
             AND m.status='active' AND m.role IN ('owner','admin') AND o.status='active' AND u.status='active') THEN
        RETURN jsonb_build_object('outcome', 'conflict');
    END IF;
    UPDATE public.change_sets SET proposed_snapshot=p_proposed_snapshot,
        audit_subject=audit_subject || jsonb_build_object('candidate_sha256', p_content_sha256),
        revision=revision+1, updated_at=now(), updated_by=p_actor_id::text, updated_by_type='user'
     WHERE id=p_change_set_id RETURNING * INTO v_row;
    SELECT COALESCE(MAX(sequence),0)+1 INTO v_sequence FROM public.change_events WHERE change_set_id=p_change_set_id;
    INSERT INTO public.change_events(change_set_id,org_id,sequence,event_type,from_status,to_status,
        actor_id,actor_type,payload) VALUES (p_change_set_id,p_org_id,v_sequence,'candidate_revised',
        'awaiting_approval','awaiting_approval',p_actor_id::text,'user',
        jsonb_build_object('content_sha256',p_content_sha256,'revision',v_row.revision));
    RETURN jsonb_build_object('outcome','replaced','change_set',to_jsonb(v_row));
END $$;
REVOKE ALL ON FUNCTION public.replace_skill_draft_candidate(UUID,UUID,UUID,BIGINT,JSONB,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.replace_skill_draft_candidate(UUID,UUID,UUID,BIGINT,JSONB,TEXT) TO everydayai;

-- Optional suggestions: evidence is append-only and never confers Skill authority.
CREATE TABLE public.skill_recommendation_audits (
    id UUID PRIMARY KEY,
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    actor_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    turn_id UUID,
    audience TEXT NOT NULL CHECK (audience IN ('user','model')),
    algorithm_version TEXT NOT NULL,
    facts JSONB NOT NULL CHECK (jsonb_typeof(facts) = 'object'),
    candidates JSONB NOT NULL CHECK (jsonb_typeof(candidates) = 'array' AND jsonb_array_length(candidates) <= 3),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX skill_recommendation_audits_scope_time ON public.skill_recommendation_audits
    (org_id,actor_user_id,conversation_id,created_at DESC);
CREATE TABLE public.skill_recommendation_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id UUID NOT NULL REFERENCES public.skill_recommendation_audits(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    feedback TEXT NOT NULL CHECK (feedback IN ('selected','dismissed','not_relevant','activated','activation_failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (recommendation_id,skill_id,revision,feedback)
);
CREATE FUNCTION public.skill_recommendation_access(organization UUID, actor UUID, conversation UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog,public AS $$
    SELECT organization = public.skill_catalog_org_id()
        AND actor = NULLIF(current_setting('app.actor_user_id', true),'')::UUID
        AND EXISTS (SELECT 1 FROM public.users u
            JOIN public.org_members m ON m.user_id=u.id AND m.org_id=organization AND m.status='active'
            JOIN public.organizations o ON o.id=m.org_id AND o.status='active'
            JOIN public.conversations c ON c.org_id=o.id AND c.id=conversation
            WHERE u.id=actor AND u.status='active' AND
                ((c.scope_type='user' AND c.user_id=actor AND c.scope_id=actor::TEXT)
                 OR (c.scope_type='channel' AND c.user_id IS NULL)))
$$;
ALTER TABLE public.skill_recommendation_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_recommendation_audits FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_recommendation_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_recommendation_feedback FORCE ROW LEVEL SECURITY;
CREATE POLICY skill_recommendations_read ON public.skill_recommendation_audits FOR SELECT TO everydayai
    USING (public.skill_recommendation_access(org_id,actor_user_id,conversation_id));
CREATE POLICY skill_recommendations_insert ON public.skill_recommendation_audits FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind',true) IN ('projection','runtime')
        AND public.skill_recommendation_access(org_id,actor_user_id,conversation_id));
CREATE POLICY skill_recommendation_feedback_read ON public.skill_recommendation_feedback FOR SELECT TO everydayai
    USING (EXISTS (SELECT 1 FROM public.skill_recommendation_audits a WHERE a.id=recommendation_id));
CREATE POLICY skill_recommendation_feedback_insert ON public.skill_recommendation_feedback FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind',true) IN ('projection','runtime') AND EXISTS (
        SELECT 1 FROM public.skill_recommendation_audits a WHERE a.id=recommendation_id
        AND a.candidates @> jsonb_build_array(jsonb_build_object('skill_id',skill_id,'revision',revision))
        AND ((a.audience='user' AND feedback IN ('selected','dismissed','not_relevant'))
            OR (a.audience='model' AND feedback IN ('activated','activation_failed')))));
REVOKE ALL ON public.skill_recommendation_audits,public.skill_recommendation_feedback FROM PUBLIC;
GRANT SELECT,INSERT ON public.skill_recommendation_audits,public.skill_recommendation_feedback TO everydayai;
REVOKE ALL ON FUNCTION public.skill_recommendation_access(UUID,UUID,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_recommendation_access(UUID,UUID,UUID) TO everydayai;

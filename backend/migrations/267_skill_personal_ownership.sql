-- Personal Skills are private to their owner. Organization and platform packages
-- keep their existing identities and review lifecycle.
ALTER TABLE public.skill_packages
    ADD COLUMN owner_user_id UUID REFERENCES public.users(id) ON DELETE RESTRICT;

DO $$
DECLARE constraint_name TEXT;
BEGIN
    FOR constraint_name IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'public.skill_packages'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%scope_kind%'
    LOOP
        EXECUTE format('ALTER TABLE public.skill_packages DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END $$;

ALTER TABLE public.skill_packages
    ADD CONSTRAINT skill_packages_scope_owner_check CHECK (
        (scope_kind = 'platform' AND org_id IS NULL AND owner_user_id IS NULL)
        OR (scope_kind = 'org' AND org_id IS NOT NULL AND owner_user_id IS NULL)
        OR (scope_kind = 'personal' AND org_id IS NULL AND owner_user_id IS NOT NULL)
    );

DROP INDEX IF EXISTS public.skill_packages_platform_key;
CREATE UNIQUE INDEX skill_packages_platform_key ON public.skill_packages(skill_key)
    WHERE scope_kind = 'platform';
CREATE UNIQUE INDEX skill_packages_personal_key ON public.skill_packages(owner_user_id, skill_key)
    WHERE scope_kind = 'personal';

CREATE OR REPLACE FUNCTION public.skill_catalog_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public
AS $$
DECLARE
    package public.skill_packages%ROWTYPE;
    expected_path TEXT;
BEGIN
    IF TG_TABLE_NAME IN ('skill_packages', 'skill_activation_audits') AND TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'SKILL_RECORD_IMMUTABLE' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'skill_revisions' AND TG_OP <> 'INSERT' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'SKILL_REVISION_IMMUTABLE' USING ERRCODE = '23514';
        END IF;
        IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status')
           OR (OLD.status = 'retired' AND NEW.status <> 'retired') THEN
            RAISE EXCEPTION 'SKILL_REVISION_IMMUTABLE' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME IN ('skill_revisions', 'skill_assignments', 'skill_activation_audits') THEN
        SELECT * INTO package FROM public.skill_packages WHERE id = NEW.package_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'SKILL_PACKAGE_UNAVAILABLE' USING ERRCODE = '23514';
        END IF;
        IF TG_TABLE_NAME = 'skill_revisions' THEN
            expected_path := CASE package.scope_kind
                WHEN 'platform' THEN 'platform'
                WHEN 'personal' THEN 'personal/' || package.owner_user_id::TEXT
                ELSE 'org/' || package.org_id::TEXT
                END || '/' || package.skill_key || '/' || NEW.revision || '/SKILL.md';
            IF NEW.nas_path <> expected_path THEN
                RAISE EXCEPTION 'SKILL_PATH_IDENTITY_MISMATCH' USING ERRCODE = '23514';
            END IF;
        ELSE
            IF package.scope_kind = 'personal' THEN
                RAISE EXCEPTION 'SKILL_PERSONAL_ASSIGNMENT_FORBIDDEN' USING ERRCODE = '23514';
            END IF;
            IF package.org_id IS NOT NULL AND package.org_id <> NEW.org_id THEN
                RAISE EXCEPTION 'SKILL_ORG_MISMATCH' USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'skill_assignments' THEN
                IF TG_OP = 'UPDATE' AND (NEW.org_id, NEW.package_id) IS DISTINCT FROM (OLD.org_id, OLD.package_id) THEN
                    RAISE EXCEPTION 'SKILL_ASSIGNMENT_IDENTITY_IMMUTABLE' USING ERRCODE = '23514';
                END IF;
                IF NEW.enabled AND NOT EXISTS (
                    SELECT 1 FROM public.skill_revisions
                    WHERE id = NEW.revision_id AND package_id = NEW.package_id AND status = 'published'
                ) THEN
                    RAISE EXCEPTION 'SKILL_REVISION_UNAVAILABLE' USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := now();
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP POLICY IF EXISTS skill_packages_read ON public.skill_packages;
CREATE POLICY skill_packages_read ON public.skill_packages FOR SELECT TO everydayai
    USING (
        scope_kind = 'platform'
        OR (scope_kind = 'org' AND org_id = public.skill_catalog_org_id())
        OR (scope_kind = 'personal' AND owner_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
    );
DROP POLICY IF EXISTS skill_packages_insert ON public.skill_packages;
CREATE POLICY skill_packages_insert ON public.skill_packages FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin' AND (
        (scope_kind = 'personal' AND org_id IS NULL AND owner_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
        OR (scope_kind = 'org' AND org_id = public.skill_catalog_org_id() AND owner_user_id IS NULL)
        OR (scope_kind = 'platform' AND org_id IS NULL AND owner_user_id IS NULL
            AND current_setting('app.skill_action', true) IN ('platform_create', 'platform_publish', 'platform_chat_publish'))
    ));

DROP POLICY IF EXISTS skill_revisions_insert ON public.skill_revisions;
CREATE POLICY skill_revisions_insert ON public.skill_revisions FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id AND (
            (p.scope_kind = 'personal' AND p.owner_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
            OR (p.scope_kind = 'org' AND p.org_id = public.skill_catalog_org_id())
            OR (p.scope_kind = 'platform' AND current_setting('app.skill_action', true) IN ('platform_create', 'platform_publish', 'platform_chat_publish'))
        )
    ));
DROP POLICY IF EXISTS skill_revisions_update ON public.skill_revisions;
CREATE POLICY skill_revisions_update ON public.skill_revisions FOR UPDATE TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id AND (
            (p.scope_kind = 'personal' AND p.owner_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
            OR (p.scope_kind = 'org' AND p.org_id = public.skill_catalog_org_id())
            OR (p.scope_kind = 'platform' AND current_setting('app.skill_action', true) LIKE 'platform_%')
        )
    ))
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id AND (
            (p.scope_kind = 'personal' AND p.owner_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
            OR (p.scope_kind = 'org' AND p.org_id = public.skill_catalog_org_id())
            OR (p.scope_kind = 'platform' AND current_setting('app.skill_action', true) LIKE 'platform_%')
        )
    ));

DROP POLICY IF EXISTS skill_drafts_owner ON public.skill_drafts;
CREATE POLICY skill_drafts_owner ON public.skill_drafts FOR ALL TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id AND (
            (p.scope_kind = 'personal' AND p.owner_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
            OR (p.scope_kind = 'org' AND p.org_id = public.skill_catalog_org_id())
            OR (p.scope_kind = 'platform' AND current_setting('app.skill_action', true) LIKE 'platform_%')
        )
    ))
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id AND (
            (p.scope_kind = 'personal' AND p.owner_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
            OR (p.scope_kind = 'org' AND p.org_id = public.skill_catalog_org_id())
            OR (p.scope_kind = 'platform' AND current_setting('app.skill_action', true) LIKE 'platform_%')
        )
    ));
DROP POLICY IF EXISTS skill_change_audits_read ON public.skill_change_audits;
CREATE POLICY skill_change_audits_read ON public.skill_change_audits FOR SELECT TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin' AND EXISTS (
        SELECT 1 FROM public.skill_packages p WHERE p.id = package_id AND (
            (p.scope_kind = 'personal' AND p.owner_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
            OR (p.scope_kind = 'org' AND p.org_id = public.skill_catalog_org_id())
            OR (p.scope_kind = 'platform' AND current_setting('app.skill_action', true) LIKE 'platform_%')
        )
    ));
DROP POLICY IF EXISTS skill_change_audits_insert ON public.skill_change_audits;
CREATE POLICY skill_change_audits_insert ON public.skill_change_audits FOR INSERT TO everydayai
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin'
        AND actor_user_id IS NOT DISTINCT FROM NULLIF(current_setting('app.actor_user_id', true), '')::UUID
        AND EXISTS (SELECT 1 FROM public.skill_packages p WHERE p.id = package_id AND (
            (p.scope_kind = 'personal' AND p.owner_user_id = actor_user_id)
            OR (p.scope_kind = 'org' AND p.org_id = public.skill_catalog_org_id())
            OR (p.scope_kind = 'platform' AND current_setting('app.skill_action', true) LIKE 'platform_%')
        )));

-- Chat proposals are private to their author until a user confirms a target.
CREATE TABLE public.skill_chat_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 256),
    org_id UUID REFERENCES public.organizations(id) ON DELETE RESTRICT,
    skill_key TEXT NOT NULL CHECK (skill_key ~ '^[a-z][a-z0-9_-]{0,63}$'),
    content JSONB NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    status TEXT NOT NULL DEFAULT 'awaiting_confirmation'
        CHECK (status IN ('awaiting_confirmation', 'awaiting_review', 'committed', 'cancelled', 'rejected', 'expired')),
    target_scope TEXT CHECK (target_scope IN ('personal', 'org', 'platform')),
    target_org_id UUID REFERENCES public.organizations(id) ON DELETE RESTRICT,
    package_id UUID REFERENCES public.skill_packages(id) ON DELETE RESTRICT,
    result JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(result) = 'object'),
    source_message_refs JSONB NOT NULL DEFAULT '[]'::JSONB CHECK (jsonb_typeof(source_message_refs) = 'array'),
    source_scope TEXT NOT NULL DEFAULT 'recent_40_messages'
        CHECK (source_scope IN ('selected_assistant_turn', 'recent_40_messages')),
    feedback_rating TEXT CHECK (feedback_rating IN ('helpful', 'not_helpful')),
    feedback_text TEXT NOT NULL DEFAULT '' CHECK (char_length(feedback_text) <= 1000),
    feedback_at TIMESTAMPTZ,
    scope_confirmed_by UUID REFERENCES public.users(id) ON DELETE RESTRICT,
    scope_selected_at TIMESTAMPTZ,
    decision_by UUID REFERENCES public.users(id) ON DELETE RESTRICT,
    decision_at TIMESTAMPTZ,
    decision_reason TEXT NOT NULL DEFAULT '' CHECK (char_length(decision_reason) <= 1000),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '24 hours',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((target_scope IS DISTINCT FROM 'org' OR target_org_id IS NOT NULL)
        AND (target_org_id IS NULL OR target_scope = 'org'))
);
CREATE INDEX skill_chat_proposals_actor_time ON public.skill_chat_proposals(actor_user_id, created_at DESC);
CREATE UNIQUE INDEX skill_chat_proposals_idempotency ON public.skill_chat_proposals(actor_user_id, conversation_id, idempotency_key);
CREATE UNIQUE INDEX skill_chat_proposals_package ON public.skill_chat_proposals(package_id) WHERE package_id IS NOT NULL;
ALTER TABLE public.skill_chat_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.skill_chat_proposals FORCE ROW LEVEL SECURITY;
CREATE POLICY skill_chat_proposals_owner ON public.skill_chat_proposals FOR ALL TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin'
        AND actor_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin'
        AND actor_user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID);
CREATE POLICY skill_chat_proposals_platform_admin_read ON public.skill_chat_proposals FOR SELECT TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin'
        AND target_scope = 'platform'
        AND (status = 'awaiting_review'
            OR (status IN ('committed', 'rejected')
                AND current_setting('app.skill_action', true) = 'platform_chat_publish'))
        AND EXISTS (SELECT 1 FROM public.users u
            WHERE u.id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID
              AND u.status = 'active' AND u.role = 'super_admin'));
CREATE POLICY skill_chat_proposals_platform_admin_update ON public.skill_chat_proposals FOR UPDATE TO everydayai
    USING (current_setting('app.access_kind', true) = 'runtime_admin'
        AND target_scope = 'platform' AND status = 'awaiting_review'
        AND EXISTS (SELECT 1 FROM public.users u
            WHERE u.id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID
              AND u.status = 'active' AND u.role = 'super_admin'))
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime_admin'
        AND target_scope = 'platform'
        AND EXISTS (SELECT 1 FROM public.users u
            WHERE u.id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID
              AND u.status = 'active' AND u.role = 'super_admin'));
REVOKE ALL ON public.skill_chat_proposals FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON public.skill_chat_proposals TO everydayai;

-- Session Skill pins may belong to organization-scoped or standalone chats.
ALTER TABLE public.conversation_skill_bindings ALTER COLUMN org_id DROP NOT NULL;
ALTER TABLE public.conversation_skill_bindings DROP CONSTRAINT IF EXISTS conversation_skill_bindings_org_id_fkey;
ALTER TABLE public.conversation_skill_bindings ADD CONSTRAINT conversation_skill_bindings_org_id_fkey
    FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION public.skill_binding_conversation_access(target UUID, organization UUID) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog, public
AS $$
    SELECT organization IS NOT DISTINCT FROM public.skill_catalog_org_id()
      AND EXISTS (SELECT 1 FROM public.users u
          WHERE u.id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID
            AND u.status = 'active')
      AND EXISTS (
        SELECT 1 FROM public.conversations c
        WHERE c.id = target AND c.org_id IS NOT DISTINCT FROM organization
          AND c.scope_type = 'user' AND c.scope_id = c.user_id::TEXT
          AND (
              (organization IS NULL AND c.user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID)
              OR (organization IS NOT NULL AND (
                  c.user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID
                  OR EXISTS (
                      SELECT 1 FROM public.org_members actor_membership
                      WHERE actor_membership.org_id = organization
                        AND actor_membership.user_id = NULLIF(current_setting('app.actor_user_id', true), '')::UUID
                        AND actor_membership.status = 'active'
                        AND actor_membership.role IN ('owner', 'admin')
                  )
              ))
          )
          AND (organization IS NULL OR EXISTS (
              SELECT 1 FROM public.organizations o JOIN public.org_members m ON m.org_id = o.id
              WHERE o.id = organization AND o.status = 'active' AND m.user_id = c.user_id
                AND m.status = 'active'
          ))
    )
$$;

CREATE OR REPLACE FUNCTION public.skill_binding_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public
AS $$
DECLARE actor UUID := NULLIF(current_setting('app.actor_user_id', true), '')::UUID;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'SKILL_BINDING_IMMUTABLE' USING ERRCODE = '23514';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('skill-binding:' || NEW.conversation_id::TEXT, 0));
    IF NOT EXISTS (
        SELECT 1 FROM public.skill_packages p
        JOIN public.skill_revisions r ON r.package_id = p.id
        LEFT JOIN public.skill_assignments a ON a.package_id = p.id AND a.revision_id = r.id
            AND a.org_id IS NOT DISTINCT FROM NEW.org_id
        WHERE p.id = NEW.package_id AND p.skill_key = NEW.skill_key
          AND r.id = NEW.revision_id AND r.status = 'published'
          AND (
            (p.scope_kind = 'personal' AND p.owner_user_id = actor)
            OR (p.scope_kind = 'org' AND p.org_id = NEW.org_id AND a.enabled)
            OR (p.scope_kind = 'platform' AND a.enabled)
          )
    ) THEN
        RAISE EXCEPTION 'SKILL_BINDING_REVISION_UNAVAILABLE' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.conversation_skill_bindings
                   WHERE conversation_id = NEW.conversation_id AND skill_key = NEW.skill_key)
       AND (SELECT count(*) FROM public.conversation_skill_bindings
            WHERE conversation_id = NEW.conversation_id) >= 4 THEN
        RAISE EXCEPTION 'SKILL_BINDING_LIMIT' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION public.skill_binding_conversation_access(UUID, UUID), public.skill_binding_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.skill_binding_conversation_access(UUID, UUID), public.skill_binding_guard() TO everydayai;

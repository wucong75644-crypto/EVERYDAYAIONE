-- Run in one transaction. Never discard working copies, audit history or states.
LOCK TABLE public.skill_packages, public.skill_revisions, public.skill_assignments,
    public.skill_drafts, public.skill_change_audits IN ACCESS EXCLUSIVE MODE;
ALTER TABLE public.skill_drafts NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_change_audits NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_revisions NO FORCE ROW LEVEL SECURITY;
SET LOCAL row_security = off;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.skill_drafts)
        OR EXISTS (SELECT 1 FROM public.skill_change_audits)
        OR EXISTS (SELECT 1 FROM public.skill_revisions WHERE status IN ('deprecated','disabled')) THEN
        RAISE EXCEPTION 'SKILL_AUTHORING_NOT_EMPTY';
    END IF;
END $$;
DROP TRIGGER skill_packages_audit ON public.skill_packages;
DROP TRIGGER skill_revisions_audit ON public.skill_revisions;
DROP TRIGGER skill_assignments_audit ON public.skill_assignments;
DROP TRIGGER skill_revision_lifecycle ON public.skill_revisions;
DROP TABLE public.skill_drafts, public.skill_change_audits;
DROP FUNCTION public.skill_authoring_guard(), public.skill_record_change();
ALTER TABLE public.skill_revisions DROP CONSTRAINT skill_revisions_status_check;
ALTER TABLE public.skill_revisions ADD CONSTRAINT skill_revisions_status_check
    CHECK (status IN ('published','retired'));
ALTER TABLE public.skill_revisions FORCE ROW LEVEL SECURITY;
SET LOCAL row_security = on;

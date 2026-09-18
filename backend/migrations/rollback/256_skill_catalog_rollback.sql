-- Run transactionally. Keep populated catalog/audit facts when rolling back code.
LOCK TABLE public.skill_packages, public.skill_revisions, public.skill_assignments,
    public.skill_activation_audits IN ACCESS EXCLUSIVE MODE;
-- The owner must inspect every tenant. On failure the transaction restores FORCE RLS.
ALTER TABLE public.skill_packages NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_revisions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_assignments NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.skill_activation_audits NO FORCE ROW LEVEL SECURITY;
SET LOCAL row_security = off;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.skill_packages)
       OR EXISTS (SELECT 1 FROM public.skill_revisions)
       OR EXISTS (SELECT 1 FROM public.skill_assignments)
       OR EXISTS (SELECT 1 FROM public.skill_activation_audits) THEN
        RAISE EXCEPTION 'SKILL_CATALOG_NOT_EMPTY';
    END IF;
END $$;
DROP TABLE public.skill_activation_audits;
DROP TABLE public.skill_assignments;
DROP TABLE public.skill_revisions;
DROP TABLE public.skill_packages;
DROP FUNCTION public.skill_catalog_guard();
DROP FUNCTION public.skill_catalog_org_id();

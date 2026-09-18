-- Do not silently discard published declarations. Keep this additive column on
-- ordinary code rollback. Empty/default-only metadata can be removed atomically.
LOCK TABLE public.skill_revisions IN ACCESS EXCLUSIVE MODE;
ALTER TABLE public.skill_revisions NO FORCE ROW LEVEL SECURITY;
SET LOCAL row_security = off;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.skill_revisions WHERE catalog_metadata <> '{}'::jsonb) THEN
        RAISE EXCEPTION 'SKILL_CATALOG_METADATA_NOT_EMPTY';
    END IF;
END $$;
ALTER TABLE public.skill_revisions DROP COLUMN catalog_metadata;
ALTER TABLE public.skill_revisions FORCE ROW LEVEL SECURITY;

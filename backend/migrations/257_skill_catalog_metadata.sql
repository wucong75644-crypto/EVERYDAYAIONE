-- Summary/policy declarations only; the existing revision guard makes this
-- column immutable. Old publications receive conservative application defaults.
ALTER TABLE public.skill_revisions
    ADD COLUMN catalog_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(catalog_metadata) = 'object');
COMMENT ON COLUMN public.skill_revisions.catalog_metadata IS
    'Validated published catalog frontmatter; never Skill body or storage configuration';

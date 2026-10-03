-- Keep proposal history intact. An update proposal records an edit operation
-- that the pre-268 schema cannot represent, so rollback must fail closed while
-- any such proposal remains.
BEGIN;
LOCK TABLE public.skill_chat_proposals IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM public.skill_chat_proposals WHERE operation = 'update'
    ) THEN
        RAISE EXCEPTION 'SKILL_CHAT_EDIT_PROPOSALS_NOT_EMPTY';
    END IF;
END $$;

DROP INDEX public.skill_chat_proposals_package;
ALTER TABLE public.skill_chat_proposals
    DROP CONSTRAINT skill_chat_proposals_target_contract,
    DROP COLUMN target_draft_version,
    DROP COLUMN target_revision,
    DROP COLUMN target_package_id,
    DROP COLUMN operation;
CREATE UNIQUE INDEX skill_chat_proposals_package ON public.skill_chat_proposals(package_id)
    WHERE package_id IS NOT NULL;
COMMIT;

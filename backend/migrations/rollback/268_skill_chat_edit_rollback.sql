-- Updates point at an existing package; clear their result link before restoring
-- the create-only uniqueness contract expected by the previous application.
UPDATE public.skill_chat_proposals SET package_id = NULL WHERE operation = 'update';
DROP INDEX public.skill_chat_proposals_package;
CREATE UNIQUE INDEX skill_chat_proposals_package ON public.skill_chat_proposals(package_id)
    WHERE package_id IS NOT NULL;
ALTER TABLE public.skill_chat_proposals
    DROP CONSTRAINT skill_chat_proposals_target_contract,
    DROP COLUMN target_draft_version,
    DROP COLUMN target_revision,
    DROP COLUMN target_package_id,
    DROP COLUMN operation;

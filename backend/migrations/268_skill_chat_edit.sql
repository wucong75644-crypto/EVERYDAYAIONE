ALTER TABLE public.skill_chat_proposals
    ADD COLUMN operation TEXT NOT NULL DEFAULT 'create'
        CHECK (operation IN ('create', 'update')),
    ADD COLUMN target_package_id UUID REFERENCES public.skill_packages(id) ON DELETE RESTRICT,
    ADD COLUMN target_revision TEXT,
    ADD COLUMN target_draft_version BIGINT;

ALTER TABLE public.skill_chat_proposals
    ADD CONSTRAINT skill_chat_proposals_target_contract CHECK (
        (operation = 'create' AND target_package_id IS NULL
            AND target_revision IS NULL AND target_draft_version IS NULL)
        OR (operation = 'update' AND target_package_id IS NOT NULL
            AND target_revision IS NOT NULL AND target_draft_version IS NOT NULL
            AND target_scope = 'personal')
    );

DROP INDEX public.skill_chat_proposals_package;
CREATE UNIQUE INDEX skill_chat_proposals_package ON public.skill_chat_proposals(package_id)
    WHERE package_id IS NOT NULL AND operation = 'create';

BEGIN;
LOCK TABLE public.skill_authoring_receipts, public.skill_draft_trial_runs IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.skill_authoring_receipts)
       OR EXISTS (SELECT 1 FROM public.skill_draft_trial_runs) THEN
        RAISE EXCEPTION 'SKILL_CHAT_CREATION_AUDIT_NOT_EMPTY';
    END IF;
END $$;
DROP FUNCTION public.replace_skill_draft_candidate(UUID,UUID,UUID,BIGINT,JSONB,TEXT);
DROP TABLE public.skill_draft_trial_runs;
DROP TABLE public.skill_authoring_receipts;
COMMIT;

BEGIN;
LOCK TABLE public.skill_recommendation_feedback, public.skill_recommendation_audits IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.skill_recommendation_audits)
       OR EXISTS (SELECT 1 FROM public.skill_recommendation_feedback) THEN
        RAISE EXCEPTION 'SKILL_RECOMMENDATION_AUDITS_NOT_EMPTY';
    END IF;
END $$;
DROP TABLE public.skill_recommendation_feedback;
DROP TABLE public.skill_recommendation_audits;
DROP FUNCTION public.skill_recommendation_access(UUID,UUID,UUID);
COMMIT;

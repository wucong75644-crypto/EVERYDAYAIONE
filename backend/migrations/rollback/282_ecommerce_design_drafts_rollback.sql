-- Roll back only when the pre-v3 planner code is active. Saved final items keep
-- their execution text; rollback discards only non-executable repair drafts.
SET LOCAL ROLE everydayai_owner;
DROP FUNCTION IF EXISTS public.finish_ecom_plan_draft_attempt(UUID,UUID,INTEGER,UUID,JSONB,JSONB,INTEGER);
ALTER TABLE public.ecom_image_plans DROP COLUMN IF EXISTS stage_drafts;
RESET ROLE;

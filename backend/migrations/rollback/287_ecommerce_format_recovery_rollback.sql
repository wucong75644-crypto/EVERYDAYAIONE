-- Stop v4 creation and drain active v4 runs before reverting the application reader.
SET LOCAL ROLE everydayai_owner;
DROP FUNCTION IF EXISTS public.complete_ecom_plan_local_draft(UUID,UUID,INTEGER,JSONB,TEXT,JSONB,TEXT,JSONB,JSONB);
DROP FUNCTION IF EXISTS public.persist_ecom_plan_format_state(UUID,UUID,INTEGER,JSONB,JSONB);
DROP FUNCTION IF EXISTS public.lock_ecom_format_plan(UUID);
RESET ROLE;

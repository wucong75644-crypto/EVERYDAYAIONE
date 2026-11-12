RESET ROLE;
SET LOCAL row_security = off;

-- Keep the new validators available while any saved direct image plan exists.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.agent_task_states
        WHERE state->'call_bindings' @> '[{"tool_name":"save_task_image_plan"}]'::jsonb) THEN
        RAISE EXCEPTION 'TASK_IMAGE_PLAN_ROLLBACK_REQUIRES_COMPATIBLE_VALIDATOR';
    END IF;
END $$;

SET LOCAL row_security = on;
SET LOCAL ROLE everydayai;

DROP FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb);
ALTER FUNCTION public.agent_task_direct_extension_valid_before376(jsonb,jsonb)
    RENAME TO agent_task_direct_extension_valid;
DROP FUNCTION public.agent_task_direct_records_valid(jsonb);
ALTER FUNCTION public.agent_task_direct_records_valid_before376(jsonb)
    RENAME TO agent_task_direct_records_valid;

-- Disable new calls before applying this rollback. Preserve saved plans and
-- acceptance receipts because accepted image workers may still be reading them.
REVOKE EXECUTE ON FUNCTION public.claim_ecom_image_plan(UUID,UUID,UUID,INTEGER) FROM everydayai;
REVOKE EXECUTE ON FUNCTION public.save_ecom_image_plan_stage(UUID,UUID,INTEGER,JSONB,JSONB,TEXT,JSONB,JSONB,INTEGER) FROM everydayai;
REVOKE EXECUTE ON FUNCTION public.accept_chat_ecom_plan_image(UUID,UUID,JSONB,UUID,UUID,INTEGER,UUID) FROM everydayai;
-- Plan tables remain as durable, read-only evidence until operators separately
-- verify that no conversation or worker references them.

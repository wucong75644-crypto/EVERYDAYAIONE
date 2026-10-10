-- Keep normally completed but invalid drafts separate from executable stages.
-- Credits, attempt receipt and latest draft commit in the same transaction.
SET LOCAL ROLE everydayai_owner;

ALTER TABLE public.ecom_image_plans ADD COLUMN stage_drafts JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(stage_drafts) = 'object');

CREATE OR REPLACE FUNCTION public.finish_ecom_plan_draft_attempt(
    p_plan_id UUID, p_lease_token UUID, p_stage INTEGER, p_attempt_id UUID,
    p_usage JSONB, p_draft JSONB, p_credits INTEGER DEFAULT 0
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result JSONB; usage JSONB;
BEGIN
    IF p_draft IS NULL OR jsonb_typeof(p_draft) <> 'object' THEN
        RAISE EXCEPTION 'ECOM_PLAN_DRAFT_INVALID' USING ERRCODE='22023';
    END IF;
    -- Included in the existing settlement fingerprint: an idempotent replay
    -- cannot replace a settled draft with a different one at the same charge.
    usage=COALESCE(p_usage,'{}'::jsonb) || jsonb_build_object('draft_fingerprint',md5(p_draft::text));
    result=public.finish_ecom_plan_attempt(p_plan_id,p_lease_token,p_stage,p_attempt_id,
        usage,'validation_failed',NULL,'planning',NULL,NULL,p_credits);
    IF result->>'outcome'='saved' THEN
        UPDATE public.ecom_image_plans SET stage_drafts=jsonb_set(stage_drafts,ARRAY[p_stage::text],p_draft),
            updated_at=NOW() WHERE id=p_plan_id;
    END IF;
    RETURN result;
END $$;

REVOKE ALL ON FUNCTION public.finish_ecom_plan_draft_attempt(UUID,UUID,INTEGER,UUID,JSONB,JSONB,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.finish_ecom_plan_draft_attempt(UUID,UUID,INTEGER,UUID,JSONB,JSONB,INTEGER) TO everydayai;
RESET ROLE;

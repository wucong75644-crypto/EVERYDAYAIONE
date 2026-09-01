-- 251: 后台规划器只能补全同一条 resolving ChangeSet 的候选内容。
-- 不接收状态、组织、资源或审计字段，状态推进仍必须经过 transition_change_set。

CREATE OR REPLACE FUNCTION public.enrich_change_set_proposal(
    p_change_set_id UUID, p_org_id UUID, p_expected_status TEXT,
    p_proposed_snapshot JSONB, p_patch JSONB, p_diff JSONB, p_risk_level TEXT,
    p_policy_snapshot JSONB, p_plan_snapshot JSONB,
    p_tool_policy_snapshot JSONB, p_check_summary JSONB
)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_row public.change_sets%ROWTYPE;
BEGIN
    UPDATE public.change_sets
       SET proposed_snapshot = p_proposed_snapshot, patch = p_patch, diff = p_diff,
           risk_level = p_risk_level, policy_snapshot = p_policy_snapshot,
           plan_snapshot = p_plan_snapshot, tool_policy_snapshot = p_tool_policy_snapshot,
           check_summary = p_check_summary, updated_at = NOW()
     WHERE id = p_change_set_id AND org_id = p_org_id AND status = p_expected_status
     RETURNING * INTO v_row;
    IF NOT FOUND THEN
        SELECT * INTO v_row FROM public.change_sets
         WHERE id = p_change_set_id AND org_id = p_org_id;
        RETURN jsonb_build_object('outcome', 'state_conflict', 'change_set', to_jsonb(v_row));
    END IF;
    RETURN jsonb_build_object('outcome', 'enriched', 'change_set', to_jsonb(v_row));
END;
$$;

REVOKE ALL ON FUNCTION public.enrich_change_set_proposal(UUID, UUID, TEXT, JSONB, JSONB, JSONB, TEXT, JSONB, JSONB, JSONB, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enrich_change_set_proposal(UUID, UUID, TEXT, JSONB, JSONB, JSONB, TEXT, JSONB, JSONB, JSONB, JSONB) TO everydayai;

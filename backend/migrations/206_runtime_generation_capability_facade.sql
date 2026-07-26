-- 206: Seal generation preparation behind one validated Runtime capability.

SET LOCAL ROLE everydayai_owner;

ALTER FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) RENAME TO _prepare_generation_owner;

REVOKE ALL ON FUNCTION _prepare_generation_owner(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION _prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION _prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON SEQUENCE task_queue_sequence_seq
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

CREATE FUNCTION prepare_generation(
    p_request_id UUID,
    p_operation TEXT,
    p_conversation_id UUID,
    p_user_id UUID,
    p_org_id UUID,
    p_turn_id UUID,
    p_input_message JSONB,
    p_output_message JSONB,
    p_tasks JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_actor_user_id() IS DISTINCT FROM p_user_id
       OR public.tenant_org_id() IS DISTINCT FROM p_org_id
       OR NOT public.tenant_user_fact_visible(p_org_id, p_user_id) THEN
        RAISE EXCEPTION 'GENERATION_PREPARE_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    RETURN public._prepare_generation_owner(
        p_request_id, p_operation, p_conversation_id, p_user_id, p_org_id,
        p_turn_id, p_input_message, p_output_message, p_tasks
    );
END;
$$;

REVOKE ALL ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) TO everydayai_runtime;

RESET ROLE;

-- 137: ContextSummary revision 原子提交
-- 依赖 120_turn_revision_foundation.sql。

CREATE OR REPLACE FUNCTION apply_context_summary(
    p_conversation_id UUID,
    p_expected_summary_revision BIGINT,
    p_through_revision BIGINT,
    p_through_message_id UUID,
    p_summary TEXT,
    p_summary_message_count INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_boundary messages%ROWTYPE;
BEGIN
    IF p_conversation_id IS NULL
       OR p_expected_summary_revision < 0
       OR p_through_revision <= 0
       OR p_through_message_id IS NULL
       OR NULLIF(BTRIM(p_summary), '') IS NULL
       OR p_summary_message_count < 0 THEN
        RAISE EXCEPTION 'SUMMARY_APPLY_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SUMMARY_CONVERSATION_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    IF v_conversation.summary_revision = p_through_revision
       AND v_conversation.summary_through_message_id = p_through_message_id THEN
        RETURN jsonb_build_object(
            'outcome', 'already_applied',
            'summary_revision', v_conversation.summary_revision
        );
    END IF;
    IF v_conversation.summary_revision <> p_expected_summary_revision THEN
        RETURN jsonb_build_object(
            'outcome', 'stale',
            'summary_revision', v_conversation.summary_revision
        );
    END IF;
    IF p_through_revision <= v_conversation.summary_revision
       OR p_through_revision > v_conversation.context_revision THEN
        RAISE EXCEPTION 'SUMMARY_REVISION_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_boundary
      FROM messages
     WHERE id = p_through_message_id;
    IF NOT FOUND
       OR v_boundary.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_boundary.role::TEXT <> 'assistant'
       OR v_boundary.status::TEXT <> 'completed'
       OR v_boundary.message_kind <> 'conversation'
       OR v_boundary.context_revision IS DISTINCT FROM p_through_revision THEN
        RAISE EXCEPTION 'SUMMARY_BOUNDARY_INVALID' USING ERRCODE = '42501';
    END IF;

    UPDATE conversations
       SET context_summary = p_summary,
           summary_revision = p_through_revision,
           summary_through_message_id = p_through_message_id,
           summary_message_count = p_summary_message_count,
           updated_at = NOW()
     WHERE id = p_conversation_id;

    RETURN jsonb_build_object(
        'outcome', 'applied',
        'summary_revision', p_through_revision,
        'through_message_id', p_through_message_id
    );
END;
$$;

REVOKE ALL ON FUNCTION apply_context_summary(
    UUID, BIGINT, BIGINT, UUID, TEXT, INTEGER
) FROM PUBLIC;
DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION apply_context_summary(
            UUID, BIGINT, BIGINT, UUID, TEXT, INTEGER
        ) TO service_role;
    END IF;
END
$grant$;

COMMENT ON FUNCTION apply_context_summary(UUID, BIGINT, BIGINT, UUID, TEXT, INTEGER)
    IS '以 conversation 行锁和 expected revision 原子提交闭合 Turn 摘要，拒绝旧结果覆盖';

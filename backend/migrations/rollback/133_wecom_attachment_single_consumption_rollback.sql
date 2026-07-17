-- 恢复迁移 131 的持续活动附件行为。
-- 已经消费为 referenced 的历史附件不会被重新激活，避免回滚制造跨轮污染。

CREATE OR REPLACE FUNCTION bind_task_attachments(
    p_task_id UUID,
    p_turn_id UUID,
    p_input_message_id UUID,
    p_conversation_id UUID,
    p_org_id UUID
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    INSERT INTO task_attachment_refs(
        org_id, task_id, turn_id, input_message_id, attachment_id,
        attachment_set_id
    )
    SELECT p_org_id, p_task_id, p_turn_id, p_input_message_id, a.id,
           a.attachment_set_id
      FROM conversation_attachment_refs a
     WHERE a.conversation_id = p_conversation_id
       AND a.org_id = p_org_id
       AND a.status = 'ready'
       AND a.reference_state = 'active'
       AND a.attachment_set_id = (
           SELECT attachment_set_id
             FROM conversation_attachment_refs
            WHERE conversation_id = p_conversation_id
              AND org_id = p_org_id
              AND status = 'ready'
              AND reference_state = 'active'
            ORDER BY created_at DESC
            LIMIT 1
       )
    ON CONFLICT (task_id, attachment_id) DO NOTHING;
    GET DIAGNOSTICS v_count = ROW_COUNT;

    UPDATE conversation_attachment_refs a
       SET last_referenced_at = NOW()
     WHERE EXISTS (
         SELECT 1
           FROM task_attachment_refs r
          WHERE r.task_id = p_task_id
            AND r.attachment_id = a.id
     );
    RETURN v_count;
END;
$$;

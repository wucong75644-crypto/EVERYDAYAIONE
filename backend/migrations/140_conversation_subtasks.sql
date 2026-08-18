-- Conversation Actor 父子任务关联与子任务终态回传。
-- 子任务自身仍由 tasks 的 claim/lease/terminal RPC 管理；父任务只消费完成事件。

CREATE TABLE IF NOT EXISTS conversation_subtask_links (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parent_task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    parent_conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    child_task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    parent_command_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result JSONB NOT NULL DEFAULT '{}'::JSONB,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT conversation_subtask_links_status_check
        CHECK (status IN ('pending', 'completed', 'failed', 'cancelled', 'ignored')),
    CONSTRAINT conversation_subtask_links_command_check
        CHECK (length(BTRIM(parent_command_id)) BETWEEN 1 AND 200),
    CONSTRAINT conversation_subtask_links_result_check
        CHECK (jsonb_typeof(result) = 'object'),
    CONSTRAINT conversation_subtask_links_parent_command_unique
        UNIQUE (parent_task_id, parent_command_id),
    CONSTRAINT conversation_subtask_links_child_unique
        UNIQUE (child_task_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_subtask_links_parent_pending
    ON conversation_subtask_links(parent_task_id, created_at)
    WHERE status = 'pending';

CREATE OR REPLACE FUNCTION register_conversation_subtask(
    p_parent_task_id UUID,
    p_parent_execution_token UUID,
    p_parent_command_id TEXT,
    p_child_task_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_parent tasks%ROWTYPE;
    v_child tasks%ROWTYPE;
    v_link conversation_subtask_links%ROWTYPE;
    v_inserted_count BIGINT;
BEGIN
    IF p_parent_task_id IS NULL OR p_parent_execution_token IS NULL
       OR p_child_task_id IS NULL
       OR NULLIF(BTRIM(p_parent_command_id), '') IS NULL THEN
        RAISE EXCEPTION 'ACTOR_SUBTASK_REGISTER_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_parent FROM tasks WHERE id = p_parent_task_id FOR UPDATE;
    SELECT * INTO v_child FROM tasks WHERE id = p_child_task_id FOR UPDATE;
    IF NOT FOUND OR v_parent.id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_SUBTASK_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_parent.type <> 'chat'
       OR v_parent.status <> 'running'
       OR v_parent.execution_token IS DISTINCT FROM p_parent_execution_token
       OR NOT (v_parent.delivery_context @> '{"actor": true}'::JSONB)
       OR v_child.id IS NULL
       OR v_child.type <> 'chat'
       OR v_child.status NOT IN ('pending', 'running')
       OR NOT (v_child.delivery_context @> '{"actor": true}'::JSONB)
       OR v_child.user_id IS DISTINCT FROM v_parent.user_id
       OR v_child.org_id IS DISTINCT FROM v_parent.org_id THEN
        RAISE EXCEPTION 'ACTOR_SUBTASK_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO conversation_subtask_links(
        parent_task_id, parent_conversation_id, child_task_id, parent_command_id
    ) VALUES (
        p_parent_task_id, v_parent.conversation_id, p_child_task_id,
        BTRIM(p_parent_command_id)
    )
    ON CONFLICT (parent_task_id, parent_command_id) DO NOTHING;
    GET DIAGNOSTICS v_inserted_count = ROW_COUNT;

    SELECT * INTO v_link
      FROM conversation_subtask_links
     WHERE parent_task_id = p_parent_task_id
       AND parent_command_id = BTRIM(p_parent_command_id);
    IF v_link.child_task_id IS DISTINCT FROM p_child_task_id THEN
        RAISE EXCEPTION 'ACTOR_SUBTASK_COMMAND_REUSE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    RETURN jsonb_build_object(
        'outcome', 'registered',
        'link_id', v_link.id,
        'already_registered', v_inserted_count = 0
    );
END;
$$;

CREATE OR REPLACE FUNCTION publish_conversation_subtask_completion()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_link conversation_subtask_links%ROWTYPE;
    v_parent tasks%ROWTYPE;
    v_status TEXT;
    v_event_status TEXT;
BEGIN
    IF OLD.status IS NOT DISTINCT FROM NEW.status
       OR NEW.status NOT IN ('completed', 'failed', 'cancelled') THEN
        RETURN NEW;
    END IF;

    SELECT * INTO v_link
      FROM conversation_subtask_links
     WHERE child_task_id = NEW.id
     FOR UPDATE;
    IF NOT FOUND OR v_link.status <> 'pending' THEN
        RETURN NEW;
    END IF;

    v_status := CASE NEW.status
        WHEN 'completed' THEN 'completed'
        WHEN 'failed' THEN 'failed'
        ELSE 'cancelled'
    END;
    SELECT * INTO v_parent FROM tasks WHERE id = v_link.parent_task_id FOR UPDATE;
    v_event_status := CASE
        WHEN v_parent.status = 'running' THEN 'pending'
        ELSE 'ignored'
    END;

    UPDATE conversation_subtask_links
       SET status = v_status,
           result = COALESCE(NEW.result, '{}'::JSONB),
           error_message = LEFT(COALESCE(NEW.error_message, ''), 2000),
           completed_at = NOW()
     WHERE id = v_link.id;

    INSERT INTO conversation_control_events(
        conversation_id, task_id, turn_id, event_type, dedupe_key, payload,
        status, applied_at
    ) VALUES (
        v_link.parent_conversation_id, v_link.parent_task_id,
        v_parent.turn_id, 'subtask_completed',
        'subtask:' || NEW.id::TEXT,
        jsonb_build_object(
            'child_task_id', NEW.id,
            'parent_command_id', v_link.parent_command_id,
            'status', v_status,
            'result', COALESCE(NEW.result, '{}'::JSONB),
            'error_message', COALESCE(NEW.error_message, '')
        ),
        v_event_status,
        CASE WHEN v_event_status = 'ignored' THEN NOW() ELSE NULL END
    ) ON CONFLICT (task_id, dedupe_key) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tasks_subtask_completion_control_event_trigger ON tasks;
CREATE TRIGGER tasks_subtask_completion_control_event_trigger
AFTER UPDATE OF status ON tasks
FOR EACH ROW
EXECUTE FUNCTION publish_conversation_subtask_completion();

REVOKE ALL ON FUNCTION register_conversation_subtask(UUID, UUID, TEXT, UUID) FROM PUBLIC;
COMMENT ON TABLE conversation_subtask_links IS
    'Conversation Actor 父子任务关联；子任务终态通过控制事件回传父 Runtime';

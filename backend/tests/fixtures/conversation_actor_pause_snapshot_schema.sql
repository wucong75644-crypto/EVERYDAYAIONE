-- 最小隔离 PostgreSQL schema：只用于 Conversation Actor PAUSE RPC 集成测试。
-- 不代表生产 schema；生产字段兼容性由迁移契约和真实测试库共同验证。

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE users (
    id UUID PRIMARY KEY,
    nickname TEXT NOT NULL
);

CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    org_id UUID,
    title TEXT,
    active_serial_task_id UUID,
    actor_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL,
    org_id UUID,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    turn_id UUID,
    reply_to_message_id UUID
);

CREATE TABLE tasks (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    org_id UUID,
    conversation_id UUID NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    external_task_id TEXT,
    client_task_id TEXT,
    execution_mode TEXT NOT NULL DEFAULT 'serial',
    execution_attempt INTEGER NOT NULL DEFAULT 0,
    delivery_context JSONB NOT NULL DEFAULT '{}'::JSONB,
    assistant_message_id UUID,
    input_message_id UUID,
    turn_id UUID,
    execution_token UUID,
    lease_expires_at TIMESTAMPTZ,
    accumulated_content TEXT,
    accumulated_blocks JSONB DEFAULT '[]'::JSONB,
    error_message TEXT,
    completed_at TIMESTAMPTZ,
    terminal_reason TEXT,
    CONSTRAINT tasks_status_check
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled'))
);

CREATE SEQUENCE conversation_control_event_sequence_seq AS BIGINT;

CREATE TABLE conversation_control_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_sequence BIGINT NOT NULL
        DEFAULT nextval('conversation_control_event_sequence_seq'),
    conversation_id UUID NOT NULL,
    task_id UUID NOT NULL,
    turn_id UUID,
    event_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    applied_execution_token UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ,
    CONSTRAINT conversation_control_events_type_check
        CHECK (event_type IN (
            'cancel', 'approval_result', 'subtask_completed', 'tool_completed'
        )),
    CONSTRAINT conversation_control_events_dedupe_check
        CHECK (length(BTRIM(dedupe_key)) BETWEEN 1 AND 200),
    CONSTRAINT conversation_control_events_payload_object_check
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT conversation_control_events_status_check
        CHECK (status IN ('pending', 'applied', 'ignored')),
    CONSTRAINT conversation_control_events_task_dedupe_unique
        UNIQUE (task_id, dedupe_key)
);

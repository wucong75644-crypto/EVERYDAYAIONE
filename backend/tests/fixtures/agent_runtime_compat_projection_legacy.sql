CREATE EXTENSION IF NOT EXISTS pgcrypto;

SET ROLE everydayai_owner;

CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');
CREATE TYPE message_status AS ENUM (
    'pending', 'generating', 'completed', 'failed'
);
CREATE TABLE messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    org_id UUID REFERENCES organizations(id),
    role message_role NOT NULL,
    content TEXT NOT NULL,
    status message_status NOT NULL DEFAULT 'completed',
    credits_cost INTEGER DEFAULT 0,
    client_request_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    turn_id UUID,
    reply_to_message_id UUID REFERENCES messages(id),
    generation_params JSONB,
    context_revision BIGINT,
    message_kind TEXT NOT NULL DEFAULT 'conversation'
);
CREATE TABLE tasks (
    id UUID PRIMARY KEY,
    external_task_id TEXT,
    client_task_id TEXT,
    user_id UUID NOT NULL REFERENCES users(id),
    org_id UUID REFERENCES organizations(id),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    credits_locked INTEGER DEFAULT 0,
    credits_used INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    request_params JSONB,
    result JSONB,
    fail_code TEXT,
    placeholder_message_id TEXT,
    accumulated_content TEXT,
    model_id TEXT,
    assistant_message_id UUID REFERENCES messages(id),
    input_message_id UUID REFERENCES messages(id),
    turn_id UUID,
    execution_mode TEXT DEFAULT 'serial',
    delivery_context JSONB DEFAULT '{}'
);
CREATE TABLE conversation_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id),
    channel TEXT NOT NULL,
    delivery_kind TEXT NOT NULL,
    target_context JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    delivered_items JSONB DEFAULT '[]',
    last_error TEXT,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (task_id, channel, delivery_kind)
);
CREATE TABLE conversation_artifacts (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    org_id UUID REFERENCES organizations(id)
);

RESET ROLE;

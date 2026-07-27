-- 220_11: Ordered compatibility Projection facts and legacy idempotency keys.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_compat_projection_checkpoints (
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    projection_kind TEXT NOT NULL
        CHECK (projection_kind IN ('web_runtime', 'wecom')),
    through_sequence BIGINT NOT NULL DEFAULT 0 CHECK (through_sequence >= 0),
    last_event_id UUID REFERENCES agent_runtime_events(id) ON DELETE RESTRICT,
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (session_id, projection_kind)
);

CREATE TABLE agent_compat_projection_results (
    outbox_id UUID PRIMARY KEY
        REFERENCES agent_projection_outbox(id) ON DELETE RESTRICT,
    event_id UUID NOT NULL
        REFERENCES agent_runtime_events(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL
        REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    projection_kind TEXT NOT NULL
        CHECK (projection_kind IN ('web_runtime', 'wecom')),
    event_sequence BIGINT NOT NULL CHECK (event_sequence > 0),
    projection_action TEXT NOT NULL CHECK (projection_action IN (
        'checkpoint_only', 'user_message', 'run_pending', 'run_running',
        'run_waiting', 'run_completed', 'run_failed', 'run_cancelled',
        'action_progress'
    )),
    message_id UUID REFERENCES messages(id) ON DELETE RESTRICT,
    task_id UUID REFERENCES tasks(id) ON DELETE RESTRICT,
    delivery_id UUID REFERENCES conversation_deliveries(id) ON DELETE RESTRICT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (session_id, projection_kind, event_sequence)
);

CREATE UNIQUE INDEX uq_messages_agent_runtime_projection
    ON messages(client_request_id)
    WHERE client_request_id LIKE 'agent-runtime:%';
CREATE UNIQUE INDEX uq_tasks_agent_runtime_projection
    ON tasks(external_task_id)
    WHERE external_task_id LIKE 'agent-runtime:%';

ALTER TABLE agent_compat_projection_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_compat_projection_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_compat_projection_checkpoints_owner_all
    ON agent_compat_projection_checkpoints
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_compat_projection_results_owner_all
    ON agent_compat_projection_results
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_compat_projection_checkpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_compat_projection_results FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    agent_compat_projection_checkpoints, agent_compat_projection_results
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

RESET ROLE;

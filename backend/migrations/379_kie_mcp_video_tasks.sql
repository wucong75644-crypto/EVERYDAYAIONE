SET LOCAL ROLE everydayai;
CREATE TABLE public.kie_mcp_video_tasks (
    id uuid PRIMARY KEY,
    request_key text NOT NULL UNIQUE,
    fingerprint text NOT NULL,
    payload jsonb NOT NULL,
    provider_task_id text UNIQUE,
    callback_candidate text,
    state text NOT NULL DEFAULT 'submission_unknown'
        CHECK (state IN ('submission_unknown','waiting','running','success','fail')),
    source_urls jsonb NOT NULL DEFAULT '[]',
    archived_urls jsonb NOT NULL DEFAULT '[]',
    storage_state text NOT NULL DEFAULT 'pending'
        CHECK (storage_state IN ('pending','stored','retry')),
    credits_consumed numeric,
    error_code text,
    lease_token uuid,
    lease_until timestamptz,
    next_check_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kie_mcp_video_tasks_pending ON public.kie_mcp_video_tasks(next_check_at)
    WHERE state NOT IN ('success','fail') OR (state='success' AND storage_state<>'stored');
REVOKE ALL ON public.kie_mcp_video_tasks FROM PUBLIC;
COMMENT ON TABLE public.kie_mcp_video_tasks IS 'Personal KIE MCP only; access via independent bearer credential, never website user billing';

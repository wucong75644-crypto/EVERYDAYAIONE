/* 231.03: unblock media projection streams poisoned by cost events. */
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_media_cost_projection_repairs (
    outbox_id UUID PRIMARY KEY REFERENCES agent_projection_outbox(id)
        ON DELETE RESTRICT,
    event_id UUID NOT NULL REFERENCES agent_runtime_events(id)
        ON DELETE RESTRICT,
    previous_status TEXT NOT NULL,
    previous_attempt_count INTEGER NOT NULL,
    previous_next_attempt_at TIMESTAMPTZ NOT NULL,
    previous_last_error_code TEXT,
    repaired_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON TABLE agent_runtime_media_cost_projection_repairs FROM PUBLIC;

/*
 * action.cost.* events are intentionally checkpoint-only for media
 * projection.  Before the worker classifier was made exact, these events
 * were sent through the action-progress path and could become dead after
 * retries, blocking later action.completed events behind the ordered stream.
 * Only replay the deterministic poison signature; unrelated dead items keep
 * the audited recovery path.
 */
INSERT INTO agent_runtime_media_cost_projection_repairs(
    outbox_id, event_id, previous_status, previous_attempt_count,
    previous_next_attempt_at, previous_last_error_code
)
SELECT outbox.id, event.id, outbox.status, outbox.attempt_count,
       outbox.next_attempt_at, outbox.last_error_code
  FROM agent_projection_outbox outbox
  JOIN agent_runtime_events event ON outbox.event_id=event.id
 WHERE outbox.projection_kind IN ('web_runtime','wecom')
   AND outbox.status='dead'
   AND outbox.last_error_code='apply_invalidparametervalue'
   AND event.event_type IN ('action.cost.reserve','action.cost.settle')
   AND (
       EXISTS (
           SELECT 1 FROM agent_runtime_media_action_bindings binding
            WHERE binding.action_id IS NOT DISTINCT FROM event.action_id
               OR binding.run_id IS NOT DISTINCT FROM event.run_id
       )
       OR EXISTS (
           SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
            WHERE binding.action_id IS NOT DISTINCT FROM event.action_id
               OR binding.run_id IS NOT DISTINCT FROM event.run_id
       )
   )
ON CONFLICT (outbox_id) DO NOTHING;

UPDATE agent_projection_outbox outbox
   SET status='pending', lease_token=NULL, lease_expires_at=NULL,
       next_attempt_at=clock_timestamp(), updated_at=clock_timestamp()
  FROM agent_runtime_media_cost_projection_repairs repair
 WHERE repair.outbox_id=outbox.id;

RESET ROLE;

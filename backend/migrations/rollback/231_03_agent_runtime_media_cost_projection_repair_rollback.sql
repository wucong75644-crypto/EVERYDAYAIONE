SET LOCAL ROLE everydayai_owner;

DO $rollback$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM agent_runtime_media_cost_projection_repairs repair
          JOIN agent_projection_outbox outbox
            ON outbox.id=repair.outbox_id
         WHERE outbox.status='delivered'
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_COST_REPAIR_ROLLBACK_AFTER_REPLAY'
            USING ERRCODE='55000';
    END IF;
END
$rollback$;

UPDATE agent_projection_outbox outbox
   SET status=repair.previous_status,
       attempt_count=repair.previous_attempt_count,
       next_attempt_at=repair.previous_next_attempt_at,
       last_error_code=repair.previous_last_error_code,
       lease_token=NULL, lease_expires_at=NULL,
       updated_at=clock_timestamp()
  FROM agent_runtime_media_cost_projection_repairs repair
 WHERE outbox.id=repair.outbox_id;

DROP TABLE agent_runtime_media_cost_projection_repairs;

RESET ROLE;

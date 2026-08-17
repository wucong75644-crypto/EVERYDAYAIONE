/* 231.04: finish the audited cost-event repair before worker replay. */
SET LOCAL ROLE everydayai_owner;

/* The normal result triggers require the projection worker session.  This
 * owner-scoped repair already has the exact checkpoint-only payload, so skip
 * those normalization triggers only for this transactional data repair. */
ALTER TABLE agent_runtime_media_projection_results
    DISABLE TRIGGER agent_runtime_media_image_normalized_event_v1;
ALTER TABLE agent_runtime_media_projection_results
    DISABLE TRIGGER agent_runtime_media_image_zbatch_result_v1;
ALTER TABLE agent_runtime_media_projection_results
    DISABLE TRIGGER agent_runtime_media_model_video_normalized_event_v1;
ALTER TABLE agent_runtime_media_projection_results
    DISABLE TRIGGER agent_runtime_media_wecom_delivery_v1;

DO $repair$
DECLARE
    item RECORD;
    checkpoint agent_runtime_media_projection_checkpoints%ROWTYPE;
BEGIN
    FOR item IN
        SELECT outbox.*, event.id AS runtime_event_id, event.sequence,
               event.event_type, event.session_id AS runtime_session_id,
               event.org_id AS runtime_org_id, event.user_id AS runtime_user_id,
               event.action_id
          FROM agent_projection_outbox outbox
          JOIN agent_runtime_events event ON event.id=outbox.event_id
          JOIN agent_runtime_media_cost_projection_repairs repair
            ON repair.outbox_id=outbox.id
         WHERE outbox.status='dead'
           AND outbox.last_error_code='apply_invalidparametervalue'
           AND event.event_type IN ('action.cost.reserve','action.cost.settle')
         ORDER BY outbox.session_id, outbox.projection_kind,
                  event.sequence, outbox.id
    LOOP
        SELECT * INTO checkpoint
          FROM agent_runtime_media_projection_checkpoints
         WHERE session_id=item.session_id
           AND projection_kind=item.projection_kind
         FOR UPDATE;
        IF checkpoint.session_id IS NULL
           OR item.sequence <= checkpoint.through_sequence
           OR EXISTS (
               SELECT 1
                 FROM agent_projection_outbox earlier
                 JOIN agent_runtime_events earlier_event
                   ON earlier_event.id=earlier.event_id
                WHERE earlier.session_id=item.session_id
                  AND earlier.projection_kind=item.projection_kind
                  AND earlier_event.sequence < item.sequence
                  AND earlier_event.sequence > checkpoint.through_sequence
                  AND earlier.status <> 'delivered'
           ) THEN
            CONTINUE;
        END IF;
        IF EXISTS (
            SELECT 1 FROM agent_runtime_media_projection_results result
             WHERE result.session_id=item.session_id
               AND result.projection_kind=item.projection_kind
               AND result.event_sequence=item.sequence
        ) THEN
            CONTINUE;
        END IF;

        INSERT INTO agent_runtime_media_projection_results(
            outbox_id,event_id,session_id,org_id,user_id,projection_kind,
            event_sequence,projection_action,action_id,message_id,task_id,
            slot_id,slot_index,slot_status,slot_revision,content_part
        ) VALUES (
            item.id,item.runtime_event_id,item.runtime_session_id,
            item.org_id,item.user_id,item.projection_kind,item.sequence,
            'checkpoint_only',item.action_id,NULL,NULL,NULL,NULL,NULL,NULL,NULL
        );
        UPDATE agent_runtime_media_projection_checkpoints
           SET through_sequence=item.sequence,
               last_event_id=item.runtime_event_id,
               state_version=state_version+1,
               updated_at=clock_timestamp()
         WHERE session_id=item.session_id
           AND projection_kind=item.projection_kind;
        UPDATE agent_projection_outbox
           SET status='delivered',
               checkpoint=jsonb_build_object(
                   'through_sequence',item.sequence,
                   'result_id',item.id,
                   'repair','media_cost_checkpoint'
               ),
               lease_token=NULL, lease_expires_at=NULL,
               delivered_at=clock_timestamp(), updated_at=clock_timestamp()
         WHERE id=item.id AND status='dead';
    END LOOP;
END
$repair$;

ALTER TABLE agent_runtime_media_projection_results
    ENABLE TRIGGER agent_runtime_media_image_normalized_event_v1;
ALTER TABLE agent_runtime_media_projection_results
    ENABLE TRIGGER agent_runtime_media_image_zbatch_result_v1;
ALTER TABLE agent_runtime_media_projection_results
    ENABLE TRIGGER agent_runtime_media_model_video_normalized_event_v1;
ALTER TABLE agent_runtime_media_projection_results
    ENABLE TRIGGER agent_runtime_media_wecom_delivery_v1;

RESET ROLE;

/* Roll back 228.08i2 only after ModelLoop image WeCom delivery is drained. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_media_action_bindings binding
          JOIN agent_actions action ON action.id=binding.action_id
          JOIN agent_runs run ON run.id=binding.run_id
          LEFT JOIN agent_runtime_prepared_media_action_bindings prepared
            ON prepared.action_id=binding.action_id
         WHERE prepared.action_id IS NULL AND action.tool_name='generate_image'
           AND COALESCE(action.policy_snapshot->>'source','model_loop')
               IN ('model_loop','runtime_executor_registry')
           AND run.capability_snapshot->>'channel'='wecom'
           AND (action.status NOT IN('completed','failed','rejected','cancelled')
                OR run.status NOT IN('completed','failed','cancelled')
                OR binding.credit_state='pending'
                OR EXISTS(
                    SELECT 1 FROM agent_action_attempts attempt
                     WHERE attempt.action_id=action.id
                       AND attempt.status IN(
                           'claimed','dispatching','accepted','unknown'
                       )
                ))
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08I2_IMAGE_WECOM_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_media_image_wecom_outbox_facts_v1 fact
          JOIN agent_projection_outbox source_outbox
            ON source_outbox.id=fact.source_outbox_id
          JOIN agent_projection_outbox delivery_outbox
            ON delivery_outbox.id=fact.delivery_outbox_id
          JOIN conversation_deliveries delivery
            ON delivery.task_id=fact.parent_task_id
           AND delivery.channel='wecom'
           AND delivery.delivery_kind='assistant_terminal'
         WHERE source_outbox.status<>'delivered'
            OR delivery_outbox.status<>'delivered'
            OR delivery.status<>'delivered'
            OR NOT EXISTS(
                SELECT 1 FROM agent_runtime_media_projection_results result
                 WHERE result.outbox_id=source_outbox.id
            )
            OR NOT EXISTS(
                SELECT 1 FROM agent_runtime_media_projection_results result
                 WHERE result.outbox_id=delivery_outbox.id
            )
    ) OR EXISTS(
        SELECT 1
          FROM agent_runtime_media_action_bindings binding
          JOIN agent_actions action ON action.id=binding.action_id
          JOIN agent_runs run ON run.id=binding.run_id
          LEFT JOIN agent_runtime_prepared_media_action_bindings prepared
            ON prepared.action_id=binding.action_id
         WHERE prepared.action_id IS NULL AND action.tool_name='generate_image'
           AND run.capability_snapshot->>'channel'='wecom'
           AND run.status IN('completed','failed','cancelled')
           AND NOT EXISTS(
               SELECT 1
                 FROM agent_runtime_media_image_wecom_outbox_facts_v1 fact
                 JOIN agent_projection_outbox source_outbox
                   ON source_outbox.id=fact.source_outbox_id
                 JOIN agent_projection_outbox delivery_outbox
                   ON delivery_outbox.id=fact.delivery_outbox_id
                 JOIN conversation_deliveries delivery
                   ON delivery.task_id=fact.parent_task_id
                WHERE fact.run_id=run.id
                  AND source_outbox.status='delivered'
                  AND delivery_outbox.status='delivered'
                  AND delivery.status='delivered'
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08I2_IMAGE_WECOM_DELIVERY_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_media_model_media_wecom_outbox_v2
    ON agent_projection_outbox;
DROP FUNCTION _derive_agent_runtime_model_media_wecom_outbox_v2();
DROP TABLE agent_runtime_media_image_wecom_outbox_facts_v1;
CREATE TRIGGER agent_runtime_media_model_video_wecom_outbox_v1
AFTER INSERT ON agent_projection_outbox
FOR EACH ROW EXECUTE FUNCTION
    _derive_agent_runtime_model_video_wecom_outbox_v1();

RESET ROLE;

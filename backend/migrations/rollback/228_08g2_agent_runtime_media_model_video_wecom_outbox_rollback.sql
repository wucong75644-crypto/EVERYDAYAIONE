/* Roll back 228.08g2 only after ModelLoop video WeCom delivery is drained. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           '_derive_agent_runtime_model_media_wecom_outbox_v2()'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08I2_MUST_ROLL_BACK_FIRST'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_prepared_media_action_bindings binding
          JOIN agent_actions action ON action.id=binding.action_id
          JOIN agent_runs run ON run.id=binding.run_id
          JOIN agent_session_commands command ON command.id=run.command_id
          JOIN tasks child_task ON child_task.id=binding.task_id
          LEFT JOIN tasks parent_task
            ON parent_task.id=NULLIF(command.payload->>'task_id','')::UUID
         WHERE binding.media_kind='video'
           AND _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
           AND run.capability_snapshot->>'channel'='wecom'
           AND (
               action.status NOT IN ('completed','failed','rejected','cancelled')
               OR run.status NOT IN ('completed','failed','cancelled')
               OR child_task.status NOT IN ('completed','failed','cancelled')
               OR parent_task.id IS NULL
               OR parent_task.status NOT IN ('completed','failed','cancelled')
               OR binding.credit_state='pending'
               OR EXISTS(
                   SELECT 1 FROM agent_action_attempts attempt
                    WHERE attempt.action_id=action.id
                      AND attempt.status IN (
                          'claimed','dispatching','accepted','unknown'
                      )
               )
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G2_WECOM_OUTBOX_IN_USE'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_prepared_media_action_bindings binding
          JOIN agent_actions action ON action.id=binding.action_id
          JOIN agent_runs run ON run.id=binding.run_id
          JOIN agent_session_commands command ON command.id=run.command_id
          JOIN tasks parent_task
            ON parent_task.id=NULLIF(command.payload->>'task_id','')::UUID
         WHERE binding.media_kind='video'
           AND _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
           AND run.capability_snapshot->>'channel'='wecom'
           AND run.status IN ('completed','failed','cancelled')
           AND NOT EXISTS(
               SELECT 1
                 FROM agent_runtime_events event
                 JOIN agent_projection_outbox source_outbox
                   ON source_outbox.event_id=event.id
                  AND source_outbox.projection_kind='web_runtime'
                 JOIN agent_runtime_media_projection_results source_result
                   ON source_result.outbox_id=source_outbox.id
                  AND source_result.event_id=event.id
                 JOIN agent_runtime_media_wecom_outbox_facts_v1 fact
                   ON fact.source_outbox_id=source_outbox.id
                  AND fact.event_id=event.id
                  AND fact.run_id=run.id
                  AND fact.anchor_action_id=action.id
                 JOIN agent_projection_outbox delivery_outbox
                   ON delivery_outbox.id=fact.delivery_outbox_id
                  AND delivery_outbox.event_id=event.id
                  AND delivery_outbox.projection_kind='wecom'
                 JOIN agent_runtime_media_projection_results delivery_result
                   ON delivery_result.outbox_id=delivery_outbox.id
                  AND delivery_result.event_id=event.id
                 JOIN conversation_deliveries delivery
                   ON delivery.task_id=parent_task.id
                  AND delivery.channel='wecom'
                  AND delivery.delivery_kind='assistant_terminal'
                WHERE event.run_id=run.id
                  AND event.session_id=run.session_id
                  AND event.org_id IS NOT DISTINCT FROM run.org_id
                  AND event.user_id IS NOT DISTINCT FROM run.user_id
                  AND event.action_id IS NULL
                  AND event.event_type='run.'||run.status
                  AND source_outbox.status='delivered'
                  AND delivery_outbox.status='delivered'
                  AND source_result.projection_action='run_'||run.status
                  AND delivery_result.projection_action='run_'||run.status
                  AND delivery.status='delivered'
           )
    ) OR EXISTS(
        SELECT 1
          FROM agent_runtime_media_wecom_outbox_facts_v1 fact
          JOIN agent_projection_outbox source_outbox
            ON source_outbox.id=fact.source_outbox_id
          JOIN agent_projection_outbox delivery_outbox
            ON delivery_outbox.id=fact.delivery_outbox_id
         WHERE source_outbox.status<>'delivered'
            OR delivery_outbox.status<>'delivered'
            OR NOT EXISTS(
                SELECT 1 FROM agent_runtime_media_projection_results result
                 WHERE result.outbox_id=source_outbox.id
            )
            OR NOT EXISTS(
                SELECT 1 FROM agent_runtime_media_projection_results result
                 WHERE result.outbox_id=delivery_outbox.id
            )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G2_WECOM_DELIVERY_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_media_model_video_wecom_outbox_v1
    ON agent_projection_outbox;
DROP FUNCTION _derive_agent_runtime_model_video_wecom_outbox_v1();
DROP TABLE agent_runtime_media_wecom_outbox_facts_v1;

RESET ROLE;

/* Roll back 228.08b only after every Runtime-media WeCom run is delivered. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (
        WITH media_runs AS (
            SELECT run_id FROM agent_runtime_media_action_bindings
            UNION
            SELECT run_id FROM agent_runtime_prepared_media_action_bindings
        ), wecom_runs AS (
            SELECT DISTINCT media.run_id
              FROM media_runs media
              JOIN agent_runs run ON run.id=media.run_id
              JOIN agent_session_commands command ON command.id=run.command_id
             WHERE run.capability_snapshot->>'channel'='wecom'
                OR EXISTS(
                       SELECT 1 FROM tasks task
                        WHERE task.delivery_context @>
                              '{"actor":false,"runtime":true,"channel":"wecom"}'::JSONB
                          AND (
                              task.id=NULLIF(command.payload->>'task_id','')::UUID
                              OR EXISTS(
                                  SELECT 1
                                    FROM agent_runtime_media_action_bindings binding
                                   WHERE binding.run_id=run.id
                                     AND task.id IN (
                                         binding.task_id,binding.chat_task_id
                                     )
                              )
                              OR EXISTS(
                                  SELECT 1
                                    FROM agent_runtime_prepared_media_action_bindings binding
                                   WHERE binding.run_id=run.id
                                     AND task.id=binding.task_id
                              )
                          )
                   )
                OR EXISTS(
                       SELECT 1
                         FROM agent_runtime_events event
                         JOIN agent_projection_outbox outbox
                           ON outbox.event_id=event.id
                        WHERE event.run_id=run.id
                          AND outbox.projection_kind='wecom'
                   )
        )
        SELECT 1
          FROM wecom_runs media
          JOIN agent_runs run ON run.id=media.run_id
         WHERE run.status NOT IN ('completed','failed','cancelled')
            OR EXISTS(
                SELECT 1 FROM agent_actions action
                 WHERE action.run_id=run.id
                   AND action.status NOT IN (
                       'completed','failed','rejected','cancelled'
                   )
            )
            OR EXISTS(
                SELECT 1
                  FROM agent_action_attempts attempt
                  JOIN agent_actions action ON action.id=attempt.action_id
                 WHERE action.run_id=run.id
                   AND attempt.status IN (
                       'claimed','dispatching','accepted','unknown'
                   )
            )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_IN_FLIGHT'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS (
        WITH media_runs AS (
            SELECT run_id FROM agent_runtime_media_action_bindings
            UNION
            SELECT run_id FROM agent_runtime_prepared_media_action_bindings
        ), wecom_runs AS (
            SELECT DISTINCT media.run_id
              FROM media_runs media
              JOIN agent_runs run ON run.id=media.run_id
              JOIN agent_session_commands command ON command.id=run.command_id
             WHERE run.capability_snapshot->>'channel'='wecom'
                OR EXISTS(
                       SELECT 1 FROM tasks task
                        WHERE task.delivery_context @>
                              '{"actor":false,"runtime":true,"channel":"wecom"}'::JSONB
                          AND (
                              task.id=NULLIF(command.payload->>'task_id','')::UUID
                              OR EXISTS(
                                  SELECT 1
                                    FROM agent_runtime_media_action_bindings binding
                                   WHERE binding.run_id=run.id
                                     AND task.id IN (
                                         binding.task_id,binding.chat_task_id
                                     )
                              )
                              OR EXISTS(
                                  SELECT 1
                                    FROM agent_runtime_prepared_media_action_bindings binding
                                   WHERE binding.run_id=run.id
                                     AND task.id=binding.task_id
                              )
                          )
                   )
                OR EXISTS(
                       SELECT 1
                         FROM agent_runtime_events event
                         JOIN agent_projection_outbox outbox
                           ON outbox.event_id=event.id
                        WHERE event.run_id=run.id
                          AND outbox.projection_kind='wecom'
                   )
        )
        SELECT 1
          FROM wecom_runs media
          JOIN agent_runs run ON run.id=media.run_id
         WHERE run.status IN ('completed','failed','cancelled')
           AND NOT EXISTS(
               SELECT 1
                 FROM agent_runtime_events event
                 JOIN agent_projection_outbox outbox
                   ON outbox.event_id=event.id
                  AND outbox.projection_kind='wecom'
                 JOIN agent_runtime_media_projection_results result
                   ON result.outbox_id=outbox.id
                  AND result.event_id=event.id
                WHERE event.run_id=run.id
                  AND event.session_id=run.session_id
                  AND event.org_id IS NOT DISTINCT FROM run.org_id
                  AND event.user_id IS NOT DISTINCT FROM run.user_id
                  AND event.action_id IS NULL
                  AND event.event_type='run.'||run.status
                  AND outbox.status='delivered'
                  AND result.projection_action IN (
                      'run_'||run.status,'checkpoint_only'
                  )
                  AND EXISTS(
                      SELECT 1 FROM conversation_deliveries delivery
                       WHERE delivery.channel='wecom'
                         AND delivery.delivery_kind='assistant_terminal'
                         AND delivery.status='delivered'
                         AND (
                             delivery.task_id=result.task_id
                             OR (result.task_id IS NULL AND EXISTS(
                                 SELECT 1 FROM agent_session_commands command
                                  JOIN tasks task ON task.id=delivery.task_id
                                 WHERE command.id=run.command_id
                                   AND task.delivery_context @>
                                       '{"actor":false,"runtime":true,"channel":"wecom"}'::JSONB
                                   AND (
                                       task.id=NULLIF(
                                           command.payload->>'task_id',''
                                       )::UUID
                                       OR EXISTS(
                                           SELECT 1
                                             FROM agent_runtime_media_action_bindings binding
                                            WHERE binding.run_id=run.id
                                              AND task.id IN (
                                                  binding.task_id,binding.chat_task_id
                                              )
                                       )
                                       OR EXISTS(
                                           SELECT 1
                                             FROM agent_runtime_prepared_media_action_bindings binding
                                            WHERE binding.run_id=run.id
                                              AND task.id=binding.task_id
                                       )
                                   )
                             ))
                         )
                  )
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_media_wecom_delivery_v1
    ON agent_runtime_media_projection_results;
DROP FUNCTION _project_agent_runtime_media_wecom_delivery_v1();

RESET ROLE;

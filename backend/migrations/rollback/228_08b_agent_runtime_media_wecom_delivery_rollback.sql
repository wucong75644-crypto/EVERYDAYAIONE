/* Roll back only the additive 228.08b Runtime-media WeCom delivery hook. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM agent_projection_outbox outbox
          JOIN agent_runtime_events event ON event.id=outbox.event_id
         WHERE outbox.projection_kind='wecom'
           AND outbox.status<>'delivered'
           AND event.event_type IN (
               'run.completed','run.failed','run.cancelled'
           )
           AND (
               EXISTS (
                   SELECT 1 FROM agent_runtime_media_action_bindings binding
                    WHERE binding.run_id=event.run_id
               )
               OR EXISTS (
                   SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
                    WHERE binding.run_id=event.run_id
               )
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_IN_FLIGHT'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_runtime_media_projection_results result
         WHERE result.projection_kind='wecom'
           AND EXISTS (
               SELECT 1 FROM agent_runtime_events event
                WHERE event.id=result.event_id
                  AND event.event_type IN (
                      'run.completed','run.failed','run.cancelled'
                  )
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_HISTORY_PRESENT'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_media_wecom_delivery_v1
    ON agent_runtime_media_projection_results;
DROP FUNCTION _project_agent_runtime_media_wecom_delivery_v1();

RESET ROLE;

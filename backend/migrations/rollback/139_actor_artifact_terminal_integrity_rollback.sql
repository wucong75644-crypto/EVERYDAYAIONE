DROP TRIGGER IF EXISTS trg_close_exhausted_actor_message ON tasks;
DROP FUNCTION IF EXISTS close_exhausted_actor_message();

DROP TRIGGER IF EXISTS trg_normalize_conversation_artifact_storage
    ON conversation_artifacts;
DROP FUNCTION IF EXISTS normalize_conversation_artifact_storage();

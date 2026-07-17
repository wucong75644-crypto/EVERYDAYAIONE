-- 124 rollback：仅在无 pending/delivering delivery 时执行。

DROP TRIGGER IF EXISTS tasks_actor_terminal_delivery_trigger ON tasks;
DROP FUNCTION IF EXISTS create_actor_terminal_delivery();
DROP FUNCTION IF EXISTS fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER);
DROP FUNCTION IF EXISTS complete_conversation_delivery(UUID, UUID, JSONB);
DROP FUNCTION IF EXISTS renew_conversation_delivery(UUID, UUID, INTEGER, JSONB);
DROP FUNCTION IF EXISTS claim_conversation_delivery(INTEGER, INTEGER);
DROP TABLE IF EXISTS conversation_deliveries;

-- 242 rollback: 仅在尚未需要保留定时任务投递审计记录时执行。
DROP FUNCTION IF EXISTS claim_scheduled_task_now(UUID, UUID);
DROP FUNCTION IF EXISTS fail_scheduled_task_delivery(UUID, UUID, TEXT, INTEGER);
DROP FUNCTION IF EXISTS complete_scheduled_task_delivery(UUID, UUID);
DROP FUNCTION IF EXISTS claim_scheduled_task_delivery(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS enqueue_scheduled_task_owner_alert(UUID, UUID, UUID, TEXT, JSONB, JSONB);
DROP FUNCTION IF EXISTS complete_scheduled_task_success(UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, INTEGER, INTEGER, INTEGER, JSONB);
DROP FUNCTION IF EXISTS refresh_scheduled_task_run_push_status(UUID);
DROP TABLE IF EXISTS scheduled_task_deliveries;

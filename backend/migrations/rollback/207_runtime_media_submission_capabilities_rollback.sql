-- Restore the pre-207 private media submission transition functions.

SET LOCAL ROLE everydayai_owner;

DROP FUNCTION attach_generation_external_task(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
);
DROP FUNCTION fail_prepared_generation_task(UUID, TEXT, TEXT, UUID);
ALTER FUNCTION _attach_generation_external_task_owner(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
) RENAME TO attach_generation_external_task;
ALTER FUNCTION _fail_prepared_generation_task_owner(
    UUID, TEXT, TEXT, UUID
) RENAME TO fail_prepared_generation_task;
REVOKE ALL ON FUNCTION attach_generation_external_task(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION fail_prepared_generation_task(
    UUID, TEXT, TEXT, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

RESET ROLE;

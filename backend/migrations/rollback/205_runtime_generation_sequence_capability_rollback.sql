-- Roll back the Web runtime queue sequence capability added by migration 205.

SET LOCAL ROLE everydayai_owner;

REVOKE USAGE ON SEQUENCE task_queue_sequence_seq FROM everydayai_runtime;

RESET ROLE;

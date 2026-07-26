-- 205: Allow Web runtime to allocate the ordered task queue position.

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON SEQUENCE task_queue_sequence_seq
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT USAGE ON SEQUENCE task_queue_sequence_seq TO everydayai_runtime;

RESET ROLE;

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_commit_video_terminal(
    TEXT, INTEGER, TEXT, JSONB, TEXT, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION worker_commit_video_terminal(
    TEXT, INTEGER, TEXT, JSONB, TEXT, TEXT
);

RESET ROLE;

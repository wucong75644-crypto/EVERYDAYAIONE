SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM agent_sync_submissions) THEN RAISE EXCEPTION 'ROLLBACK_GUARD_SYNC_SUBMISSIONS_EXIST'; END IF;
END $$;
REVOKE ALL ON FUNCTION create_or_get_agent_sync_submission(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT),record_agent_sync_submission_result(UUID,TEXT,TEXT,TEXT,TEXT,JSONB),recover_agent_sync_submission(TEXT,TEXT) FROM everydayai_agent_runtime_worker;
DROP FUNCTION recover_agent_sync_submission(TEXT,TEXT);
DROP FUNCTION record_agent_sync_submission_result(UUID,TEXT,TEXT,TEXT,TEXT,JSONB);
DROP FUNCTION create_or_get_agent_sync_submission(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT);
DROP TABLE agent_sync_submissions;
RESET ROLE;

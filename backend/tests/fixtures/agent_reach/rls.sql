BEGIN;
SET LOCAL ROLE everydayai_runtime;
SELECT set_config('app.org_id','00000000-0000-0000-0000-000000000001',true),set_config('app.actor_user_id','00000000-0000-0000-0000-000000000011',true);
INSERT INTO reach_connections(id,org_id,platform,account_id,created_by) VALUES ('00000000-0000-0000-0000-000000000021','00000000-0000-0000-0000-000000000001','twitter','test','00000000-0000-0000-0000-000000000011');
DO $$ BEGIN IF (SELECT count(*) FROM reach_connections) <> 1 THEN RAISE EXCEPTION 'own org visibility failed'; END IF; END $$;
SELECT set_config('app.org_id','00000000-0000-0000-0000-000000000002',true),set_config('app.actor_user_id','00000000-0000-0000-0000-000000000012',true);
DO $$ BEGIN IF (SELECT count(*) FROM reach_connections) <> 0 THEN RAISE EXCEPTION 'cross org visibility'; END IF; END $$;
DO $$ BEGIN
  BEGIN INSERT INTO reach_connections(org_id,platform,account_id,created_by) VALUES ('00000000-0000-0000-0000-000000000001','twitter','forged','00000000-0000-0000-0000-000000000012'); RAISE EXCEPTION 'cross-org write unexpectedly succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
ROLLBACK;

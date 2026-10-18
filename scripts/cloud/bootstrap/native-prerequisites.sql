-- Native development prerequisite definitions; never a production migration.
-- Exact current message contract documented in
-- docs/document/TECH_统一多模态消息系统重构方案.md section 2.2;
-- consumers: schemas/message.py and message_gateway. No new status vocabulary.
ALTER TABLE messages ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'completed';
ALTER TABLE messages ADD COLUMN task_id VARCHAR(100);
ALTER TABLE messages ADD COLUMN error JSONB;
ALTER TABLE messages ADD COLUMN updated_at TIMESTAMPTZ;
-- 024 is a legacy data conversion program requiring Supabase. This bootstrap
-- targets an empty table, so use the documented final JSONB storage contract.
ALTER TABLE messages ALTER COLUMN content TYPE JSONB USING to_jsonb(content);

-- Same transaction-local UUID identity set by core/db_scope.py's
-- SET_DATABASE_SCOPE_SQL. These accessors do NOT authorize an actor themselves;
-- all existing session_user/membership/RLS checks in current SQL stay intact.
CREATE FUNCTION public.tenant_actor_user_id() RETURNS UUID
LANGUAGE SQL STABLE SET search_path=pg_catalog,public
AS $$ SELECT NULLIF(current_setting('app.actor_user_id',true),'')::UUID $$;
CREATE FUNCTION public.tenant_org_id() RETURNS UUID
LANGUAGE SQL STABLE SET search_path=pg_catalog,public
AS $$ SELECT NULLIF(current_setting('app.org_id',true),'')::UUID $$;
REVOKE ALL ON FUNCTION public.tenant_actor_user_id(),public.tenant_org_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tenant_actor_user_id(),public.tenant_org_id()
TO everydayai,everydayai_owner,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker;

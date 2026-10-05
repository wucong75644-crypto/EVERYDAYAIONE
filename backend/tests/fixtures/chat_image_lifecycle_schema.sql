-- Minimal isolated schema for real transaction/RLS tests. Not a production dump.
CREATE EXTENSION pgcrypto;
CREATE TABLE users(id UUID PRIMARY KEY, role TEXT DEFAULT 'user', status TEXT DEFAULT 'active', credits INTEGER NOT NULL, updated_at TIMESTAMPTZ);
CREATE TABLE organizations(id UUID PRIMARY KEY,status TEXT DEFAULT 'active');
CREATE TABLE org_members(org_id UUID, user_id UUID, status TEXT DEFAULT 'active',role TEXT DEFAULT 'admin', PRIMARY KEY(org_id,user_id));
CREATE TABLE conversations(id UUID PRIMARY KEY,user_id UUID,org_id UUID,scope_type TEXT DEFAULT 'user',context_revision BIGINT DEFAULT 0);
CREATE TABLE messages(id UUID PRIMARY KEY,conversation_id UUID,org_id UUID,role TEXT,content TEXT,status TEXT,generation_params JSONB,context_revision BIGINT,credits_cost INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),message_kind TEXT DEFAULT 'conversation');
CREATE TABLE credit_transactions(id UUID PRIMARY KEY,task_id UUID UNIQUE,user_id UUID,org_id UUID,amount INTEGER CHECK(amount>0),type TEXT,status TEXT,reason TEXT,expires_at TIMESTAMPTZ,confirmed_at TIMESTAMPTZ);
CREATE TYPE credits_change_type AS ENUM ('refund');
CREATE TABLE credits_history(user_id UUID,org_id UUID,change_amount INTEGER,balance_after INTEGER,change_type credits_change_type,description TEXT);
CREATE TABLE change_sets(id UUID,org_id UUID,created_by TEXT,status TEXT,resource_type TEXT,revision BIGINT,
    proposed_snapshot JSONB,audit_subject JSONB,expires_at TIMESTAMPTZ DEFAULT NOW()+INTERVAL '1 day',PRIMARY KEY(org_id,id));
CREATE TABLE tasks(id UUID PRIMARY KEY,user_id UUID,org_id UUID,conversation_id UUID,type TEXT,status TEXT,
    model_id TEXT,assistant_message_id UUID,placeholder_message_id TEXT,batch_id TEXT,image_index INTEGER,
    request_params JSONB,delivery_context JSONB,execution_token UUID,turn_id UUID,input_message_id UUID,
    base_context_revision BIGINT,credit_transaction_id UUID REFERENCES credit_transactions(id),
    credits_locked INTEGER DEFAULT 0,credits_used INTEGER DEFAULT 0,version INTEGER DEFAULT 1,created_at TIMESTAMPTZ DEFAULT NOW(),started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,external_task_id TEXT,error_message TEXT,result JSONB,result_data JSONB);
GRANT USAGE ON SCHEMA public TO everydayai;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO everydayai;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY test_task_scope ON tasks TO everydayai USING (
    org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::UUID
    AND (current_setting('app.access_kind',TRUE) IN ('worker','runtime_admin') OR user_id=NULLIF(current_setting('app.actor_user_id',TRUE),'')::UUID)
) WITH CHECK (
    org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::UUID
    AND (current_setting('app.access_kind',TRUE)='worker' OR user_id=NULLIF(current_setting('app.actor_user_id',TRUE),'')::UUID)
);
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions FORCE ROW LEVEL SECURITY;
CREATE POLICY test_credit_scope ON credit_transactions TO everydayai USING (
    org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::UUID
) WITH CHECK (org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::UUID);

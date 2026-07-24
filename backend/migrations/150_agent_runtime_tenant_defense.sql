-- 150: Agent Runtime 首组 13 表租户纵深防御。
-- 前置：管理员已执行 deploy/transfer-agent-runtime-ownership.sh。
-- 本阶段创建身份辅助函数和 RLS policy；FORCE RLS 在独立切换任务中启用。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION tenant_actor_user_id()
RETURNS UUID
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN pg_input_is_valid(
            current_setting('app.actor_user_id', TRUE), 'uuid'
        )
        THEN current_setting('app.actor_user_id', TRUE)::UUID
        ELSE NULL
    END
$$;

CREATE OR REPLACE FUNCTION tenant_org_id()
RETURNS UUID
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN pg_input_is_valid(current_setting('app.org_id', TRUE), 'uuid')
        THEN current_setting('app.org_id', TRUE)::UUID
        ELSE NULL
    END
$$;

CREATE OR REPLACE FUNCTION tenant_database_role_matches_scope()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT CASE session_user
        WHEN 'everydayai_runtime' THEN
            current_setting('app.access_kind', TRUE) = 'runtime'
        WHEN 'everydayai_worker' THEN
            current_setting('app.access_kind', TRUE) = 'worker'
        ELSE FALSE
    END
$$;

CREATE OR REPLACE FUNCTION tenant_actor_is_active_member(p_org_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT p_org_id IS NOT NULL
       AND tenant_actor_user_id() IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM public.org_members member
             JOIN public.organizations organization
               ON organization.id = member.org_id
            WHERE member.org_id = p_org_id
              AND member.user_id = tenant_actor_user_id()
              AND member.status = 'active'
              AND organization.status = 'active'
       )
$$;

CREATE OR REPLACE FUNCTION tenant_user_fact_visible(
    p_org_id UUID,
    p_user_id UUID
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT tenant_database_role_matches_scope()
       AND tenant_actor_user_id() IS NOT NULL
       AND p_user_id = tenant_actor_user_id()
       AND (
           (p_org_id IS NULL AND tenant_org_id() IS NULL)
           OR (
               p_org_id = tenant_org_id()
               AND tenant_actor_is_active_member(p_org_id)
           )
       )
$$;

CREATE OR REPLACE FUNCTION tenant_conversation_visible(
    p_conversation_id UUID,
    p_child_org_id UUID
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT tenant_database_role_matches_scope()
       AND EXISTS (
           SELECT 1
             FROM public.conversations conversation
            WHERE conversation.id = p_conversation_id
              AND conversation.org_id IS NOT DISTINCT FROM p_child_org_id
              AND (
                  (
                      conversation.scope_type = 'user'
                      AND conversation.user_id = tenant_actor_user_id()
                      AND (
                          (
                              conversation.org_id IS NULL
                              AND tenant_org_id() IS NULL
                          )
                          OR (
                              conversation.org_id = tenant_org_id()
                              AND tenant_actor_is_active_member(
                                  conversation.org_id
                              )
                          )
                      )
                  )
                  OR (
                      conversation.scope_type = 'channel'
                      AND conversation.org_id = tenant_org_id()
                      AND tenant_actor_is_active_member(conversation.org_id)
                  )
              )
       )
$$;

CREATE OR REPLACE FUNCTION tenant_task_visible(
    p_task_id UUID,
    p_child_org_id UUID
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT tenant_database_role_matches_scope()
       AND EXISTS (
           SELECT 1
             FROM public.tasks task
            WHERE task.id = p_task_id
              AND task.org_id IS NOT DISTINCT FROM p_child_org_id
              AND tenant_conversation_visible(
                  task.conversation_id,
                  task.org_id
              )
       )
$$;

CREATE OR REPLACE FUNCTION tenant_asset_visible(
    p_org_id UUID,
    p_storage_scope TEXT,
    p_storage_owner_key TEXT
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT tenant_database_role_matches_scope()
       AND (
           (
               p_storage_scope = 'user'
               AND p_storage_owner_key = tenant_actor_user_id()::TEXT
               AND (
                   (p_org_id IS NULL AND tenant_org_id() IS NULL)
                   OR (
                       p_org_id = tenant_org_id()
                       AND tenant_actor_is_active_member(p_org_id)
                   )
               )
           )
           OR (
               p_storage_scope = 'channel'
               AND p_org_id = tenant_org_id()
               AND tenant_actor_is_active_member(p_org_id)
           )
       )
$$;

CREATE OR REPLACE FUNCTION tenant_asset_ref_visible(p_asset_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT tenant_database_role_matches_scope()
       AND EXISTS (
           SELECT 1
             FROM public.user_assets asset
            WHERE asset.id = p_asset_id
              AND tenant_asset_visible(
                  asset.org_id,
                  asset.storage_scope,
                  asset.storage_owner_key
              )
       )
$$;

REVOKE ALL ON FUNCTION tenant_actor_user_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_org_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_database_role_matches_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_actor_is_active_member(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_user_fact_visible(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_conversation_visible(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_task_visible(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_asset_visible(UUID, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_asset_ref_visible(UUID) FROM PUBLIC;

ALTER TABLE conversation_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_attachment_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_channel_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_compactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_context_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_context_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_data_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_generation_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_attachment_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_atoms ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_asset_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_activity_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_conversation_artifacts
    ON conversation_artifacts TO everydayai_runtime, everydayai_worker
    USING (tenant_conversation_visible(conversation_id, org_id))
    WITH CHECK (tenant_conversation_visible(conversation_id, org_id));
CREATE POLICY tenant_conversation_attachment_refs
    ON conversation_attachment_refs TO everydayai_runtime, everydayai_worker
    USING (tenant_conversation_visible(conversation_id, org_id))
    WITH CHECK (tenant_conversation_visible(conversation_id, org_id));
CREATE POLICY tenant_conversation_channel_bindings
    ON conversation_channel_bindings TO everydayai_runtime, everydayai_worker
    USING (tenant_conversation_visible(conversation_id, org_id))
    WITH CHECK (tenant_conversation_visible(conversation_id, org_id));
CREATE POLICY tenant_conversation_compactions
    ON conversation_compactions TO everydayai_runtime, everydayai_worker
    USING (tenant_conversation_visible(conversation_id, org_id))
    WITH CHECK (tenant_conversation_visible(conversation_id, org_id));
CREATE POLICY tenant_conversation_context_items
    ON conversation_context_items TO everydayai_runtime, everydayai_worker
    USING (tenant_conversation_visible(conversation_id, org_id))
    WITH CHECK (tenant_conversation_visible(conversation_id, org_id));
CREATE POLICY tenant_conversation_context_receipts
    ON conversation_context_receipts TO everydayai_runtime, everydayai_worker
    USING (tenant_conversation_visible(conversation_id, org_id))
    WITH CHECK (tenant_conversation_visible(conversation_id, org_id));
CREATE POLICY tenant_conversation_data_evidence
    ON conversation_data_evidence TO everydayai_runtime, everydayai_worker
    USING (tenant_conversation_visible(conversation_id, org_id))
    WITH CHECK (tenant_conversation_visible(conversation_id, org_id));
CREATE POLICY tenant_message_generation_requests
    ON message_generation_requests TO everydayai_runtime, everydayai_worker
    USING (tenant_user_fact_visible(org_id, user_id))
    WITH CHECK (tenant_user_fact_visible(org_id, user_id));
CREATE POLICY tenant_task_attachment_refs
    ON task_attachment_refs TO everydayai_runtime, everydayai_worker
    USING (tenant_task_visible(task_id, org_id))
    WITH CHECK (tenant_task_visible(task_id, org_id));
CREATE POLICY tenant_memory_atoms
    ON memory_atoms TO everydayai_runtime, everydayai_worker
    USING (tenant_user_fact_visible(org_id, user_id))
    WITH CHECK (tenant_user_fact_visible(org_id, user_id));
CREATE POLICY tenant_user_assets
    ON user_assets TO everydayai_runtime, everydayai_worker
    USING (tenant_asset_visible(org_id, storage_scope, storage_owner_key))
    WITH CHECK (tenant_asset_visible(org_id, storage_scope, storage_owner_key));
CREATE POLICY tenant_user_asset_refs
    ON user_asset_refs TO everydayai_runtime, everydayai_worker
    USING (tenant_asset_ref_visible(asset_id))
    WITH CHECK (tenant_asset_ref_visible(asset_id));
CREATE POLICY tenant_user_activity_events
    ON user_activity_events TO everydayai_runtime, everydayai_worker
    USING (tenant_user_fact_visible(org_id, user_id))
    WITH CHECK (tenant_user_fact_visible(org_id, user_id));

RESET ROLE;

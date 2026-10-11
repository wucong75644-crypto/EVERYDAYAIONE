-- All active members can use organization connections. Administration remains
-- restricted to owner/admin; member/user/org activity is checked by the helper.
SET LOCAL ROLE everydayai_owner;
DROP POLICY reach_operation_insert ON public.reach_operations;
CREATE POLICY reach_operation_insert ON public.reach_operations FOR INSERT
    WITH CHECK (public.reach_active_member(org_id)
        AND actor_user_id=NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid
        AND EXISTS (SELECT 1 FROM public.reach_connections c WHERE c.id=connection_id
            AND c.org_id=reach_operations.org_id AND c.status='active'
            AND c.credential_version=reach_operations.credential_version));
RESET ROLE;

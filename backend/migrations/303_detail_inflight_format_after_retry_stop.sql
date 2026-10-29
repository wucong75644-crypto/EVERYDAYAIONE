-- Stopping automatic delivery retries does not cancel the active planning lease.
-- Keep ownership, run identity, archive and lease checks at their existing boundaries.
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.lock_ecom_format_plan(p_plan_id UUID)
RETURNS public.ecom_image_plans LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; d public.detail_projects%ROWTYPE; project UUID; root UUID;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
  RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT project_id,COALESCE(root_plan_id,id) INTO project,root FROM public.ecom_image_plans WHERE id=p_plan_id;
 SELECT * INTO d FROM public.detail_projects WHERE id=project FOR UPDATE;
 PERFORM 1 FROM public.ecom_image_plans WHERE id=root FOR UPDATE;
 SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
 IF p.id IS NULL OR d.id IS NULL OR p.source_kind IS DISTINCT FROM 'detail_project'
  OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
  OR p.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
  OR d.user_id IS DISTINCT FROM p.user_id OR d.org_id IS DISTINCT FROM p.org_id
  OR d.status='archived' OR d.run_state->>'run_id' IS DISTINCT FROM p.generation_run_id::text THEN
  RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 RETURN p;
END $$;

RESET ROLE;

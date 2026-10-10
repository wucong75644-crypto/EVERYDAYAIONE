-- Restore draft-only attachments without deleting input files or generation history.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.attach_detail_project_image_by_id(p_project_id UUID,p_workspace_path TEXT,p_category TEXT)
RETURNS TABLE(project_id UUID,project_version INTEGER,image_id UUID)
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE d public.detail_projects%ROWTYPE; actor UUID:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org UUID:=NULLIF(current_setting('app.org_id',true),'')::uuid; n INTEGER; pos SMALLINT; image UUID;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' OR actor IS NULL THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_project_id AND user_id=actor AND org_id IS NOT DISTINCT FROM org FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'DETAIL_PROJECT_DENIED' USING ERRCODE='42501'; END IF;
 IF d.status<>'draft' THEN RAISE EXCEPTION 'DETAIL_PROJECT_NOT_DRAFT' USING ERRCODE='55000'; END IF;
 IF p_workspace_path IS NULL OR length(p_workspace_path) NOT BETWEEN 1 AND 500 OR p_category IS NULL OR p_category NOT IN ('product','reference') THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_INVALID_PATH' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM public.detail_project_images i WHERE i.project_id=d.id AND i.workspace_path=p_workspace_path) THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_DUPLICATE' USING ERRCODE='23505'; END IF;
 SELECT COUNT(*),COALESCE(MAX(sort_order)+1,0) INTO n,pos FROM public.detail_project_images i WHERE i.project_id=d.id;
 IF n>=9 THEN RAISE EXCEPTION 'DETAIL_IMAGE_LIMIT_EXCEEDED' USING ERRCODE='22023'; END IF;
 INSERT INTO public.detail_project_images(project_id,user_id,org_id,workspace_path,category,sort_order)
 VALUES(d.id,actor,org,p_workspace_path,p_category,pos) RETURNING id INTO image;
 UPDATE public.detail_projects SET version=version+1,updated_at=NOW() WHERE id=d.id RETURNING version INTO d.version;
 RETURN QUERY SELECT d.id,d.version,image;
END $$;
RESET ROLE;

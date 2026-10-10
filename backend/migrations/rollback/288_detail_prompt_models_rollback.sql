-- Never replace a user's selected model to make a downgrade succeed.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.detail_projects WHERE prompt_model='gpt-6-luna')
 OR EXISTS(SELECT 1 FROM public.ecom_image_plans WHERE source_kind='detail_project'
   AND model_settings->>'model'='gpt-6-luna') THEN
  RAISE EXCEPTION 'DETAIL_PROMPT_MODEL_ROLLBACK_UNSAFE: retain compatible app and model records';
 END IF;
END $$;
SET LOCAL ROLE everydayai;
ALTER TABLE public.detail_projects DROP CONSTRAINT detail_projects_prompt_model_check;
ALTER TABLE public.detail_projects ADD CONSTRAINT detail_projects_prompt_model_check
 CHECK(prompt_model IN ('kimi-k3','gemini-3.8-flash'));
ALTER TABLE public.detail_projects ALTER COLUMN prompt_model SET DEFAULT 'kimi-k3';
RESET ROLE;

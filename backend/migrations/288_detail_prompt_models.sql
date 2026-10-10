-- New drafts use Gemini; persisted drafts and frozen run profiles retain their model.
SET LOCAL ROLE everydayai;
ALTER TABLE public.detail_projects DROP CONSTRAINT detail_projects_prompt_model_check;
ALTER TABLE public.detail_projects ADD CONSTRAINT detail_projects_prompt_model_check
 CHECK(prompt_model IN ('kimi-k3','gemini-3.8-flash','gpt-6-luna'));
ALTER TABLE public.detail_projects ALTER COLUMN prompt_model SET DEFAULT 'gemini-3.8-flash';
RESET ROLE;

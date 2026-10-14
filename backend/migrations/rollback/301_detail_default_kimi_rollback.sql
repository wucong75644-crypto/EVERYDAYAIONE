-- Restore only the new-task default. Existing tasks and runs remain unchanged.
SET LOCAL ROLE everydayai;
ALTER TABLE public.detail_projects ALTER COLUMN prompt_model SET DEFAULT 'gemini-3.8-flash';
RESET ROLE;

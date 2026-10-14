-- New page tasks default to Kimi; preserve saved choices and frozen run profiles.
SET LOCAL ROLE everydayai;
ALTER TABLE public.detail_projects ALTER COLUMN prompt_model SET DEFAULT 'kimi-k3';
RESET ROLE;

-- Restores the old constraint; cannot recreate task/draft data already deleted
-- by a user-confirmed deletion. Application rollback needs no constraint rollback.
ALTER TABLE public.scheduled_task_drafts
    DROP CONSTRAINT scheduled_task_drafts_confirmed_task_id_fkey,
    ADD CONSTRAINT scheduled_task_drafts_confirmed_task_id_fkey
        FOREIGN KEY (confirmed_task_id)
        REFERENCES public.scheduled_tasks(id);

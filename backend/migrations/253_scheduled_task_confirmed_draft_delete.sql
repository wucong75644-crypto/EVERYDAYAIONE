-- Creation drafts must share the task lifecycle, just like source_task_id in
-- migration 245. Otherwise an old confirmed draft prevents the ChangeSet delete
-- RPC from deleting its task. Draft/preflight intermediates cascade; ChangeSet
-- events and scheduled_task_change_receipts retain their independent audit trail.
ALTER TABLE public.scheduled_task_drafts
    DROP CONSTRAINT scheduled_task_drafts_confirmed_task_id_fkey,
    ADD CONSTRAINT scheduled_task_drafts_confirmed_task_id_fkey
        FOREIGN KEY (confirmed_task_id)
        REFERENCES public.scheduled_tasks(id) ON DELETE CASCADE;

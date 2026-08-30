-- 248 回滚：仅允许在没有 ChangeSet 数据时执行，避免丢失审计时间线。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.change_sets)
       OR EXISTS (SELECT 1 FROM public.change_checks)
       OR EXISTS (SELECT 1 FROM public.change_events) THEN
        RAISE EXCEPTION 'cannot roll back 248 while ChangeSet data exists';
    END IF;
END $$;

DROP FUNCTION IF EXISTS public.record_change_check(UUID, UUID, UUID, TEXT, TEXT, TEXT, JSONB, JSONB, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.transition_change_set(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB);
DROP FUNCTION IF EXISTS public.create_change_set(UUID, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, JSONB, JSONB, JSONB, TEXT, JSONB, JSONB, JSONB, JSONB, TEXT, TIMESTAMPTZ, TEXT, TEXT, JSONB, UUID);
DROP TABLE IF EXISTS public.change_events;
DROP TABLE IF EXISTS public.change_checks;
DROP TABLE IF EXISTS public.change_sets;

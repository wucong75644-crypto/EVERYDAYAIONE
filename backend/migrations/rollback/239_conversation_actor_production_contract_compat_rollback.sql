-- 239 rollback is intentionally non-destructive.
-- conversation_turn_checkpoints and the pause/resume/checkpoint RPCs are part of
-- the production Actor contract and may predate this migration. They must not be
-- dropped during an application rollback. Only the additive uncertain helper is
-- removed; existing checkpoint rows and control facts remain untouched.

DROP FUNCTION IF EXISTS public.mark_stale_tool_invocation_uncertain(
    UUID, UUID, TEXT, UUID, INTEGER
);

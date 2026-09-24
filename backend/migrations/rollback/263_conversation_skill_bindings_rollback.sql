-- Run transactionally; never erase populated user configuration during rollback.
LOCK TABLE public.conversation_skill_bindings IN ACCESS EXCLUSIVE MODE;
ALTER TABLE public.conversation_skill_bindings NO FORCE ROW LEVEL SECURITY;
SET LOCAL row_security = off;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.conversation_skill_bindings) THEN
        RAISE EXCEPTION 'SKILL_BINDINGS_NOT_EMPTY';
    END IF;
END $$;
DROP TABLE public.conversation_skill_bindings;
DROP FUNCTION public.skill_binding_guard();
DROP FUNCTION public.skill_binding_conversation_access(UUID, UUID);

DROP TRIGGER IF EXISTS tasks_conversation_delivery_status_trigger ON public.tasks;
DROP FUNCTION IF EXISTS public.sync_conversation_delivery_status();
DROP FUNCTION IF EXISTS public.read_conversation_delivery_state(UUID, UUID, BIGINT);
DROP FUNCTION IF EXISTS public.save_conversation_delivery_snapshot(UUID, UUID, TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_conversation_delivery_event(UUID, UUID, TEXT, JSONB);
DROP FUNCTION IF EXISTS public.begin_conversation_delivery_session(UUID, UUID, INTEGER, UUID);
DROP TABLE IF EXISTS public.conversation_delivery_events;
DROP TABLE IF EXISTS public.conversation_delivery_sessions;

DROP FUNCTION IF EXISTS public.append_conversation_delivery_event(
    UUID, UUID, TEXT, JSONB, UUID
);
DROP INDEX IF EXISTS public.idx_conversation_delivery_events_stream_event;
ALTER TABLE public.conversation_delivery_events
    DROP COLUMN IF EXISTS event_id;

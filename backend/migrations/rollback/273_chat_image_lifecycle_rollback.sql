-- Non-destructive rollback: keep schema, indexes and RPCs for in-flight tasks.
-- Disable CHAT_IMAGE_ASYNC_ENABLED and roll back only to a compatible reader.
-- Do not drop task snapshots or refund uncertain requests automatically.
SELECT 'Retain chat image lifecycle foundation; disable new acceptance at application layer';

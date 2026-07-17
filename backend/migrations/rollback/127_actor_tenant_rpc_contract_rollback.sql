DROP FUNCTION IF EXISTS enqueue_wecom_generation_turn(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
);
DROP FUNCTION IF EXISTS enqueue_generation_turn(
    JSONB, UUID, UUID, TEXT, JSONB, UUID
);
DROP FUNCTION IF EXISTS close_generation_turn(
    UUID, UUID, UUID, UUID
);
DROP FUNCTION IF EXISTS bind_generation_turn(
    UUID, UUID, UUID, UUID, TEXT, UUID
);

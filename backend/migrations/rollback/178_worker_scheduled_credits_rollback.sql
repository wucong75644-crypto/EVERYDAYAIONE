SET LOCAL ROLE everydayai_owner;

DROP FUNCTION worker_settle_scheduled_credits(
    UUID, UUID, UUID, BOOLEAN, INTEGER
);
DROP FUNCTION worker_lock_scheduled_credits(UUID, UUID);

RESET ROLE;

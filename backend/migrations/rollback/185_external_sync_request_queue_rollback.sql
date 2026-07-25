SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS sync_renew_external_sync(UUID, UUID, INTEGER);
DROP FUNCTION IF EXISTS sync_finish_external_sync(
    UUID, UUID, BOOLEAN, TEXT
);
DROP FUNCTION IF EXISTS sync_claim_external_sync(INTEGER);
DROP FUNCTION IF EXISTS runtime_enqueue_external_sync(
    UUID, TEXT, TEXT, DATE, DATE, TEXT
);
DROP TABLE IF EXISTS kuaimai_external_sync_requests;

RESET ROLE;

-- Roll back only the isolated-role grants. Function bodies remain safe.

SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION claim_message_generation_request(
    UUID, UUID, UUID, VARCHAR, CHAR, VARCHAR, UUID
) FROM everydayai_runtime;
REVOKE EXECUTE ON FUNCTION cleanup_expired_message_generation_requests()
FROM everydayai_worker;

GRANT EXECUTE ON FUNCTION claim_message_generation_request(
    UUID, UUID, UUID, VARCHAR, CHAR, VARCHAR, UUID
) TO everydayai;
GRANT EXECUTE ON FUNCTION cleanup_expired_message_generation_requests()
TO everydayai;

RESET ROLE;

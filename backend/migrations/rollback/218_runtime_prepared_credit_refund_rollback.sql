SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION refund_prepared_generation_credits(
    UUID, UUID, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
DROP FUNCTION refund_prepared_generation_credits(UUID, UUID, UUID);

RESET ROLE;

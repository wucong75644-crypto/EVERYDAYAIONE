SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION worker_replace_global_knowledge_seed(JSONB)
FROM everydayai_worker;
DROP FUNCTION worker_replace_global_knowledge_seed(JSONB);
DROP FUNCTION _validate_global_knowledge_seed_payload(JSONB);

RESET ROLE;

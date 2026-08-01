SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_policy_receipts)
       OR EXISTS (SELECT 1 FROM agent_authorization_grant_uses)
       OR EXISTS (SELECT 1 FROM agent_authorization_grants)
       OR EXISTS (SELECT 1 FROM agent_interactions) THEN
        RAISE EXCEPTION 'AGENT_AUTHORIZATION_ROLLBACK_HAS_FACTS'
            USING ERRCODE = '55000';
    END IF;
END
$guard$;

DROP TABLE agent_policy_receipts;
DROP TABLE agent_authorization_grant_uses;
DROP TABLE agent_authorization_grants;
DROP TABLE agent_interactions;

RESET ROLE;

SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS
    get_claimed_agent_action_reconciliation(TEXT),
    claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER),
    get_agent_action_snapshot_batch(TEXT, TEXT),
    claim_ready_agent_action_snapshots(TEXT, TEXT, INTEGER, INTEGER),
    _agent_action_dispatch_snapshot(agent_action_attempts);

RESET ROLE;

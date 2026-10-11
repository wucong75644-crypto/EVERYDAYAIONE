-- Login health is separate from authorization/revocation. Additive rollback:
-- disable Reach; preserve connection envelopes and operation receipts.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE public.reach_connections
    ADD COLUMN login_state TEXT NOT NULL DEFAULT 'unchecked'
        CHECK (login_state IN ('unchecked','connected','expired','error')),
    ADD COLUMN checked_at TIMESTAMPTZ;
CREATE UNIQUE INDEX reach_active_platform_account ON public.reach_connections(org_id,platform,account_id)
    WHERE status='active' AND scope='organization';
RESET ROLE;

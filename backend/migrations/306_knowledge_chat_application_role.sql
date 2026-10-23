-- The local application pool connects as everydayai; keep the same actor/org
-- RLS boundary for it and the dedicated runtime role, as in migrations 302/303.
-- Forward-only fix: 305 may already be recorded in the production ledger.
SET LOCAL ROLE everydayai_owner;
ALTER POLICY kb_snapshot_actor ON public.kb_read_snapshots TO everydayai,everydayai_runtime;
ALTER POLICY kb_receipt_actor ON public.kb_read_receipts TO everydayai,everydayai_runtime;
GRANT SELECT,INSERT ON public.kb_read_snapshots,public.kb_read_receipts TO everydayai;
GRANT USAGE,SELECT ON SEQUENCE public.kb_read_receipts_id_seq TO everydayai;
GRANT EXECUTE ON FUNCTION public.kb_chat_file(uuid,uuid,bigint),public.kb_lock_read_snapshot(jsonb) TO everydayai;
RESET ROLE;

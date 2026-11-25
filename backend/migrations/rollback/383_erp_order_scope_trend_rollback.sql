-- Additive overload only; existing six-argument summary/compare RPC remains.
DROP FUNCTION IF EXISTS erp_order_stats_grouped(UUID,TIMESTAMPTZ,TIMESTAMPTZ,VARCHAR,JSONB,VARCHAR,VARCHAR);

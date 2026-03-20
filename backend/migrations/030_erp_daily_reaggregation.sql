-- 030: ERP 每日聚合兜底 RPC
-- 对近 N 天内有变更的 (outer_id, stat_date) 重新聚合 daily_stats
-- 设计文档: docs/document/TECH_ERP数据本地索引系统.md §5.1

CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats_batch(
    p_since_date DATE
) RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INTEGER := 0;
    v_rec RECORD;
BEGIN
    FOR v_rec IN
        SELECT DISTINCT
            outer_id,
            (doc_created_at::DATE)::TEXT AS stat_date
        FROM erp_document_items
        WHERE doc_created_at >= p_since_date
          AND outer_id IS NOT NULL
    LOOP
        PERFORM erp_aggregate_daily_stats(v_rec.outer_id, v_rec.stat_date);
        v_count := v_count + 1;
    END LOOP;

    RETURN v_count;
END;
$$;

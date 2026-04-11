-- 058: 修复 erp_aggregate_daily_stats 并发竞态
--
-- 根因：DELETE+INSERT 模式在 READ COMMITTED 隔离级别下存在 read-then-write 竞态窗口。
-- 两条调用路径同时聚合同一 (outer_id, stat_date, org_id) 时：
--   T1: BEGIN → DELETE(0行) → INSERT R1（持有 unique 索引锁，未提交）
--   T2: BEGIN → DELETE（看不到 R1，因未提交）→ INSERT 阻塞在 R1 索引锁
--   T1: COMMIT → T2 解阻塞 → R1 已存在 → duplicate key violation ❌
--
-- 触发场景（生产 04-11 实际命中）：
--   路径 A：erp_sync_orchestrator._aggregation_consumer（增量入库后实时聚合）
--   路径 B：erp_sync_executor._run_daily_reaggregation
--           → erp_aggregate_daily_stats_batch
--           → SQL LOOP 调用 erp_aggregate_daily_stats（同一 key）
--
-- 修复：函数体首句加事务级 advisory lock，按 (outer_id, stat_date, org_id) 哈希
--      串行化同 key 的并发调用，不同 key 仍可并行。
--      锁键 = bigint，事务结束自动释放，无泄漏风险。
--
-- 风险：函数签名不变，调用方零改动；advisory lock 是 PG 内存级整数锁，零额外 IO。

CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats(
    p_outer_id VARCHAR, p_stat_date DATE, p_org_id UUID DEFAULT NULL
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    -- ── 事务级 advisory lock：按 (outer_id, stat_date, org_id) 串行化 ──
    -- hashtextextended 返回 bigint，避免 hashtext 的 32-bit 碰撞风险
    -- 锁随事务 COMMIT/ROLLBACK 自动释放
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            p_outer_id || '|' || p_stat_date::text || '|' || COALESCE(p_org_id::text, ''),
            0
        )
    );

    -- 删除旧记录（按 org_id 隔离）
    DELETE FROM erp_product_daily_stats
    WHERE stat_date = p_stat_date AND outer_id = p_outer_id
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id);

    -- 插入新聚合数据
    INSERT INTO erp_product_daily_stats (
        stat_date, outer_id, sku_outer_id, item_name, org_id,
        purchase_count, purchase_qty, purchase_received_qty, purchase_amount,
        receipt_count, receipt_qty, shelf_count, shelf_qty,
        purchase_return_count, purchase_return_qty, purchase_return_amount,
        aftersale_count, aftersale_refund_count, aftersale_return_count,
        aftersale_exchange_count, aftersale_reissue_count,
        aftersale_reject_count, aftersale_repair_count, aftersale_other_count,
        aftersale_qty, aftersale_amount,
        order_count, order_qty, order_amount,
        order_shipped_count, order_finished_count,
        order_refund_count, order_cancelled_count, order_cost, updated_at
    )
    SELECT
        p_stat_date, p_outer_id, NULL, MAX(item_name), p_org_id,
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(quantity_received) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'receipt'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'receipt'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'shelf'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'shelf'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase_return'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale'),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type IN (1, 5)),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 2),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 4),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 3),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 7),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 9),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type IN (0, 8)),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'aftersale'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'aftersale'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'order'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'order'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND consign_time IS NOT NULL),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND order_status = 'FINISHED'),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND refund_status IS NOT NULL),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND (extra_json->>'isCancel')::int = 1),
        COALESCE(SUM(cost * quantity) FILTER(WHERE doc_type = 'order'), 0),
        NOW()
    FROM erp_document_items
    WHERE outer_id = p_outer_id
      AND doc_created_at >= p_stat_date
      AND doc_created_at < p_stat_date + INTERVAL '1 day'
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id);
END;
$$;

COMMENT ON FUNCTION erp_aggregate_daily_stats IS
    'ERP每日聚合（多租户）。事务级 advisory lock 串行化同 (outer_id, stat_date, org_id) 的并发调用。';

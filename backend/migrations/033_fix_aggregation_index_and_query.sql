-- ============================================================
-- 033: 修复聚合查询超时问题
--
-- 问题：erp_aggregate_daily_stats 使用 doc_created_at::date = p_stat_date
--       类型转换导致无法命中索引，57万行表全表扫描 → statement timeout
--
-- 修复：
--   1. 新建复合索引 (outer_id, doc_created_at) 精确覆盖聚合查询
--   2. 将 ::date 转换改为范围查询，命中索引的 Range Scan
-- ============================================================

-- Step 1: 新建复合索引
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_doc_items_outer_created
    ON erp_document_items (outer_id, doc_created_at);

-- Step 2: 重建聚合函数，将 ::date 改为范围查询
CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats(
    p_outer_id VARCHAR,
    p_stat_date DATE
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO erp_product_daily_stats (
        stat_date, outer_id, sku_outer_id, item_name,
        purchase_count, purchase_qty, purchase_received_qty, purchase_amount,
        receipt_count, receipt_qty,
        shelf_count, shelf_qty,
        purchase_return_count, purchase_return_qty, purchase_return_amount,
        aftersale_count, aftersale_refund_count, aftersale_return_count,
        aftersale_exchange_count, aftersale_reissue_count,
        aftersale_reject_count, aftersale_repair_count, aftersale_other_count,
        aftersale_qty, aftersale_amount,
        order_count, order_qty, order_amount,
        order_shipped_count, order_finished_count,
        order_refund_count, order_cancelled_count, order_cost,
        updated_at
    )
    SELECT
        p_stat_date,
        p_outer_id,
        NULL,  -- SPU 级汇总
        MAX(item_name),
        -- 采购
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(quantity_received) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase'), 0),
        -- 收货
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'receipt'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'receipt'), 0),
        -- 上架
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'shelf'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'shelf'), 0),
        -- 采退
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase_return'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase_return'), 0),
        -- 售后
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
        -- 订单
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
    ON CONFLICT (stat_date, outer_id, COALESCE(sku_outer_id, ''))
    DO UPDATE SET
        item_name = EXCLUDED.item_name,
        purchase_count = EXCLUDED.purchase_count,
        purchase_qty = EXCLUDED.purchase_qty,
        purchase_received_qty = EXCLUDED.purchase_received_qty,
        purchase_amount = EXCLUDED.purchase_amount,
        receipt_count = EXCLUDED.receipt_count,
        receipt_qty = EXCLUDED.receipt_qty,
        shelf_count = EXCLUDED.shelf_count,
        shelf_qty = EXCLUDED.shelf_qty,
        purchase_return_count = EXCLUDED.purchase_return_count,
        purchase_return_qty = EXCLUDED.purchase_return_qty,
        purchase_return_amount = EXCLUDED.purchase_return_amount,
        aftersale_count = EXCLUDED.aftersale_count,
        aftersale_refund_count = EXCLUDED.aftersale_refund_count,
        aftersale_return_count = EXCLUDED.aftersale_return_count,
        aftersale_exchange_count = EXCLUDED.aftersale_exchange_count,
        aftersale_reissue_count = EXCLUDED.aftersale_reissue_count,
        aftersale_reject_count = EXCLUDED.aftersale_reject_count,
        aftersale_repair_count = EXCLUDED.aftersale_repair_count,
        aftersale_other_count = EXCLUDED.aftersale_other_count,
        aftersale_qty = EXCLUDED.aftersale_qty,
        aftersale_amount = EXCLUDED.aftersale_amount,
        order_count = EXCLUDED.order_count,
        order_qty = EXCLUDED.order_qty,
        order_amount = EXCLUDED.order_amount,
        order_shipped_count = EXCLUDED.order_shipped_count,
        order_finished_count = EXCLUDED.order_finished_count,
        order_refund_count = EXCLUDED.order_refund_count,
        order_cancelled_count = EXCLUDED.order_cancelled_count,
        order_cost = EXCLUDED.order_cost,
        updated_at = NOW();
END;
$$;

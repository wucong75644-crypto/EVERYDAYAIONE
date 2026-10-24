-- Durable nightly batches. Application-side publication is a single transaction.
CREATE TABLE erp_sync_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID,
    org_key TEXT NOT NULL,
    business_date DATE NOT NULL,
    source_as_of TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','failed','complete')),
    progress JSONB NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,
    completed_at TIMESTAMPTZ,
    UNIQUE(org_key,business_date),
    CHECK (org_key = COALESCE(org_id::text,'__default__'))
);
CREATE INDEX erp_sync_batches_pending ON erp_sync_batches(org_key,business_date) WHERE status <> 'complete';
CREATE TABLE erp_sync_staged_documents (
    batch_id UUID NOT NULL REFERENCES erp_sync_batches(id) ON DELETE CASCADE,
    org_id UUID,
    doc_type TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    rows_json JSONB NOT NULL,
    PRIMARY KEY(batch_id,doc_type,doc_id)
);
ALTER TABLE erp_document_items ADD COLUMN source_updated_at TIMESTAMP;
ALTER TABLE erp_document_items_archive ADD COLUMN source_updated_at TIMESTAMP;
-- Store one refund header per work order; never SUM repeated headers in SKU rows.
CREATE TABLE erp_registered_refunds (
    org_key TEXT NOT NULL,
    org_id UUID,
    work_order_id TEXT NOT NULL,
    order_sid TEXT,
    platform_refund_id TEXT,
    original_pay_time TIMESTAMP,
    refund_money NUMERIC,
    platform_refund_money NUMERIC,
    work_status TEXT,
    online_status TEXT,
    source_modified_at TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(org_key,work_order_id),
    CHECK (org_key = COALESCE(org_id::text,'__default__'))
);
CREATE INDEX erp_registered_refunds_payment ON erp_registered_refunds(org_key,original_pay_time);
GRANT SELECT,INSERT,UPDATE,DELETE ON erp_sync_batches,erp_sync_staged_documents,erp_registered_refunds TO everydayai;

-- Retain creation-day semantics, but include archived facts after historical corrections.
CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats(
    p_outer_id VARCHAR, p_stat_date DATE, p_org_id UUID DEFAULT NULL
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    -- 事务级 advisory lock：按 (outer_id, stat_date, org_id) 串行化
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            p_outer_id || '|' || p_stat_date::text || '|' || COALESCE(p_org_id::text, ''),
            0
        )
    );

    -- 删除该商品当天所有行（含所有 platform/shop_name 组合）
    DELETE FROM erp_product_daily_stats
    WHERE stat_date = p_stat_date AND outer_id = p_outer_id
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id);

    -- 按 (platform, shop_name) 分组插入多行
    -- 采购/收货/上架/采退 通常无 platform/shop_name，会归入 (NULL, NULL) 行
    -- 订单/售后 有 platform + shop_name，按实际值分组
    INSERT INTO erp_product_daily_stats (
        stat_date, outer_id, sku_outer_id, item_name, org_id,
        platform, shop_name,
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
        p_stat_date,
        p_outer_id,
        NULL,                   -- sku_outer_id: SPU 级汇总
        MAX(item_name),
        p_org_id,
        platform,               -- ★ 聚合维度
        shop_name,              -- ★ 聚合维度
        -- 采购（采购单通常无 platform，归入 NULL 行）
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
    FROM (SELECT doc_id,doc_type,outer_id,item_name,platform,shop_name,org_id,doc_created_at,quantity,quantity_received,amount,aftersale_type,consign_time,order_status,refund_status,extra_json,cost FROM erp_document_items
          UNION ALL SELECT doc_id,doc_type,outer_id,item_name,platform,shop_name,org_id,doc_created_at,quantity,quantity_received,amount,aftersale_type,consign_time,order_status,refund_status,extra_json,cost FROM erp_document_items_archive) facts
    WHERE outer_id = p_outer_id
      AND doc_created_at >= p_stat_date
      AND doc_created_at < p_stat_date + INTERVAL '1 day'
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id)
    GROUP BY platform, shop_name;  -- ★ 按平台+店铺分组
END;
$$;


-- Historical refund attribution only; no implicit full-refund/valid-order policy.
CREATE VIEW erp_registered_refund_daily AS
SELECT org_id, original_pay_time::date AS payment_date, work_status, online_status,
       COUNT(*) AS work_order_count, SUM(refund_money) AS system_registered_refund,
       SUM(platform_refund_money) AS platform_registered_refund
FROM erp_registered_refunds
GROUP BY org_id,original_pay_time::date,work_status,online_status;
GRANT SELECT ON erp_registered_refund_daily TO everydayai;

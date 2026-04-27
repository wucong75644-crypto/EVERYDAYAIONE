-- 107: daily_stats 聚合粒度扩展——加 platform + shop_name 作为聚合维度
--
-- 背景：v2.2 查询架构重构需要按平台/店铺分组的趋势和跨域指标分析。
-- 现有 daily_stats 按 (stat_date, outer_id) 聚合，跨平台/店铺数据混在一起，
-- 无法准确回答"淘宝退货率 vs 京东退货率"这类问题。
--
-- 方案：
-- 1. ALTER TABLE 加 platform + shop_name 两列
-- 2. 重建唯一约束：(stat_date, outer_id, sku_outer_id, platform, shop_name, org_id)
-- 3. 重写聚合函数：GROUP BY 加 platform + shop_name，每个组合一行
-- 4. 加索引（按平台/店铺查趋势）
-- 5. 历史数据通过重跑 batch 聚合回填
--
-- 行数影响：大部分商品只在 1-2 个平台 × 1-2 个店铺，行数增加约 2-3 倍。
-- 性能影响：有索引覆盖，查询性能不受影响。

-- ── 1. 加列 ──
ALTER TABLE erp_product_daily_stats
    ADD COLUMN IF NOT EXISTS platform VARCHAR(32),
    ADD COLUMN IF NOT EXISTS shop_name VARCHAR(256);

-- ── 2. 重建唯一约束（platform + shop_name 加入聚合键）──
-- 先删旧约束
DROP INDEX IF EXISTS uq_daily_stats;
DROP INDEX IF EXISTS uq_daily_stats_org;

-- 新约束：COALESCE 处理 NULL（采购单可能无 platform/shop_name）
CREATE UNIQUE INDEX uq_daily_stats_v2 ON erp_product_daily_stats (
    org_id, stat_date, outer_id,
    COALESCE(sku_outer_id, ''),
    COALESCE(platform, ''),
    COALESCE(shop_name, '')
);

-- ── 3. 加查询索引 ──
CREATE INDEX IF NOT EXISTS idx_daily_stats_platform
    ON erp_product_daily_stats (org_id, platform, stat_date)
    WHERE platform IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_daily_stats_shop
    ON erp_product_daily_stats (org_id, shop_name, stat_date)
    WHERE shop_name IS NOT NULL;

-- ── 4. 重写聚合函数：按 (outer_id, platform, shop_name) 分组，每组一行 ──
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
    FROM erp_document_items
    WHERE outer_id = p_outer_id
      AND doc_created_at >= p_stat_date
      AND doc_created_at < p_stat_date + INTERVAL '1 day'
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id)
    GROUP BY platform, shop_name;  -- ★ 按平台+店铺分组
END;
$$;

COMMENT ON FUNCTION erp_aggregate_daily_stats IS
    'ERP每日聚合（多租户）。v2.2: 按 (outer_id, platform, shop_name) 分组，每个组合一行。';

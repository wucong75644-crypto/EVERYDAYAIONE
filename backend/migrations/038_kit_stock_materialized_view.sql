-- 038: 套件库存物化视图
-- 套件(item_type=1)无独立库存，库存由子单品决定。
-- 计算逻辑：套件可售 = MIN(子单品可售 / 组成数量)（木桶原理）
-- 数据来源：erp_products.suit_singles + erp_stock_status
-- 刷新时机：stock 同步完成后 REFRESH MATERIALIZED VIEW CONCURRENTLY
-- 性能：先聚合再 JOIN 名称字段，REFRESH < 1s

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_kit_stock AS
WITH kit_components AS (
    -- 展开套件子单品关系
    SELECT
        p.outer_id                              AS kit_outer_id,
        comp->>'skuOuterId'                     AS kit_sku_outer_id,
        comp->>'outerId'                        AS sub_code,
        GREATEST((comp->>'ratio')::int, 1)      AS ratio
    FROM erp_products p,
         jsonb_array_elements(p.suit_singles) AS comp
    WHERE p.item_type = 1
      AND p.suit_singles IS NOT NULL
      AND p.active_status = 1
      AND comp->>'skuOuterId' IS NOT NULL
      AND comp->>'skuOuterId' != ''
),
sub_stock AS (
    -- 子单品库存汇总（跨仓库求和）
    -- 注意：当前所有子单品均在单一仓库，若未来出现多仓需改为按仓分别计算
    SELECT
        sku_outer_id                AS sub_code,
        SUM(sellable_num)           AS total_sellable,
        SUM(total_stock)            AS total_stock,
        SUM(purchase_num)           AS total_onway
    FROM erp_stock_status
    WHERE sku_outer_id != ''
    GROUP BY sku_outer_id
),
kit_stock AS (
    -- 先聚合库存（仅2列 GROUP BY，性能关键）
    SELECT
        kc.kit_outer_id,
        kc.kit_sku_outer_id,
        MIN(FLOOR(COALESCE(ss.total_sellable, 0) / kc.ratio))::int  AS sellable_num,
        MIN(FLOOR(COALESCE(ss.total_stock, 0)    / kc.ratio))::int  AS total_stock,
        MIN(FLOOR(COALESCE(ss.total_onway, 0)    / kc.ratio))::int  AS purchase_num
    FROM kit_components kc
    LEFT JOIN sub_stock ss ON ss.sub_code = kc.sub_code
    GROUP BY kc.kit_outer_id, kc.kit_sku_outer_id
)
-- 聚合后再 JOIN 名称字段（24K 行 JOIN，不影响性能）
SELECT
    ks.kit_outer_id         AS outer_id,
    ks.kit_sku_outer_id     AS sku_outer_id,
    p.title                 AS item_name,
    ps.properties_name,
    ''::varchar             AS warehouse_id,
    ks.sellable_num,
    ks.total_stock,
    0                       AS lock_stock,
    ks.purchase_num,
    CASE
        WHEN ks.sellable_num <= 0 THEN 3
        WHEN ks.sellable_num < 10 THEN 2
        ELSE 1
    END                     AS stock_status
FROM kit_stock ks
LEFT JOIN erp_products p ON p.outer_id = ks.kit_outer_id
LEFT JOIN erp_product_skus ps ON ps.sku_outer_id = ks.kit_sku_outer_id;

-- CONCURRENTLY 刷新必需的唯一索引
CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_kit_stock
    ON mv_kit_stock (outer_id, sku_outer_id);

-- 查询索引（local_stock_query 按 sku_outer_id 或 outer_id 查）
CREATE INDEX IF NOT EXISTS idx_mv_kit_stock_sku
    ON mv_kit_stock (sku_outer_id);

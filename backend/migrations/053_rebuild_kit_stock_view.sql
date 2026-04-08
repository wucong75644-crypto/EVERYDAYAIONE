-- 053: 重建 mv_kit_stock 物化视图（加入 org_id 隔离）
-- 多租户隔离架构 P3 — 技术方案 §6.3
--
-- ⚠️ 必须在维护窗口执行！
-- ⚠️ DROP 到 CREATE 完成期间，套件库存查询（erp_local_query.py）会报错
-- ⚠️ 预计耗时 5-30 秒（取决于数据量）
--
-- 变更内容：
-- - kit_components / sub_stock / kit_stock CTE 全部加 org_id 列
-- - LEFT JOIN 追加 org_id 匹配条件
-- - 唯一索引含 COALESCE(org_id, ...)
--
-- 回滚方式：执行 backend/migrations/038_kit_stock_materialized_view.sql 恢复旧版

DROP MATERIALIZED VIEW IF EXISTS mv_kit_stock;

CREATE MATERIALIZED VIEW mv_kit_stock AS
WITH kit_components AS (
    -- 展开套件子单品关系（按企业隔离）
    SELECT
        p.org_id,
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
    -- 子单品库存汇总（按企业+SKU 聚合）
    SELECT
        org_id,
        sku_outer_id            AS sub_code,
        SUM(sellable_num)       AS total_sellable,
        SUM(total_stock)        AS total_stock,
        SUM(purchase_num)       AS total_onway
    FROM erp_stock_status
    WHERE sku_outer_id != ''
    GROUP BY org_id, sku_outer_id
),
kit_stock AS (
    -- 套件库存 = MIN(子单品可售 / 组成数量)（木桶原理，按企业隔离）
    SELECT
        kc.org_id,
        kc.kit_outer_id,
        kc.kit_sku_outer_id,
        MIN(FLOOR(COALESCE(ss.total_sellable, 0) / kc.ratio))::int  AS sellable_num,
        MIN(FLOOR(COALESCE(ss.total_stock, 0)    / kc.ratio))::int  AS total_stock,
        MIN(FLOOR(COALESCE(ss.total_onway, 0)    / kc.ratio))::int  AS purchase_num
    FROM kit_components kc
    LEFT JOIN sub_stock ss ON ss.sub_code = kc.sub_code AND ss.org_id = kc.org_id
    GROUP BY kc.org_id, kc.kit_outer_id, kc.kit_sku_outer_id
)
-- 聚合后 JOIN 名称字段
SELECT
    ks.org_id,
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
LEFT JOIN erp_products p ON p.outer_id = ks.kit_outer_id AND p.org_id = ks.org_id
LEFT JOIN erp_product_skus ps ON ps.sku_outer_id = ks.kit_sku_outer_id AND ps.org_id = ks.org_id;

-- CONCURRENTLY 刷新必需的唯一索引（含 org_id）
CREATE UNIQUE INDEX uq_mv_kit_stock
    ON mv_kit_stock (
        outer_id, sku_outer_id,
        COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
    );

-- 查询索引
CREATE INDEX idx_mv_kit_stock_sku ON mv_kit_stock (sku_outer_id);
CREATE INDEX idx_mv_kit_stock_org ON mv_kit_stock (org_id);

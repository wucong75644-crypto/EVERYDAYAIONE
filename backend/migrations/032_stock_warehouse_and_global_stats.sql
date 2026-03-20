-- 032: 库存多仓支持 + 全局统计 RPC
-- 技术文档: V2补充优化方案
-- 解决问题:
--   C: erp_stock_status UNIQUE 不含 warehouse_id，多仓 upsert 覆盖
--   D: 全局统计 LIMIT 5000 截断（日均3万行只拿<1%数据）

-- ════════════════════════════════════════════
-- C. 库存表多仓 UNIQUE 修复
-- ════════════════════════════════════════════

-- 1. 修复现有 NULL 数据
UPDATE erp_stock_status SET warehouse_id = '' WHERE warehouse_id IS NULL;

-- 2. 改为 NOT NULL DEFAULT ''（与 sku_outer_id 设计一致）
ALTER TABLE erp_stock_status ALTER COLUMN warehouse_id SET DEFAULT '';
ALTER TABLE erp_stock_status ALTER COLUMN warehouse_id SET NOT NULL;

-- 3. 删旧 UNIQUE + 建新（纯列名，PostgREST on_conflict 可匹配）
DROP INDEX IF EXISTS uq_stock_outer_sku;
CREATE UNIQUE INDEX uq_stock_outer_sku
  ON erp_stock_status (outer_id, sku_outer_id, warehouse_id);

-- ════════════════════════════════════════════
-- D. 全局统计 RPC + 专用索引
-- ════════════════════════════════════════════

-- 全局统计专用索引（现有索引都含 outer_id/sku_outer_id 前缀，全表统计无法走索引）
CREATE INDEX IF NOT EXISTS idx_doc_items_type_date
  ON erp_document_items (doc_type, doc_created_at DESC);

-- 归档表同步建索引
CREATE INDEX IF NOT EXISTS idx_archive_items_type_date
  ON erp_document_items_archive (doc_type, doc_created_at DESC);

-- 全局统计 RPC：DB 端聚合，避免 LIMIT 截断
CREATE OR REPLACE FUNCTION erp_global_stats_query(
    p_doc_type VARCHAR,
    p_start TIMESTAMPTZ,
    p_end TIMESTAMPTZ,
    p_shop VARCHAR DEFAULT NULL,
    p_platform VARCHAR DEFAULT NULL,
    p_supplier VARCHAR DEFAULT NULL,
    p_warehouse VARCHAR DEFAULT NULL,
    p_group_by VARCHAR DEFAULT NULL,
    p_limit INT DEFAULT 20
) RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    result JSONB;
    base_q TEXT;
    group_col TEXT;
    name_col TEXT;
    need_archive BOOLEAN;
BEGIN
    -- 输入校验
    IF p_start > p_end THEN
        RETURN jsonb_build_object('error', 'p_start must <= p_end');
    END IF;

    -- 90天前数据可能已归档
    need_archive := (p_start < NOW() - INTERVAL '90 days');

    -- 基础查询（只取聚合需要的列）
    base_q := format(
        'SELECT doc_id, quantity, amount, outer_id, item_name,
                shop_name, platform, supplier_name, warehouse_name,
                doc_status, order_status
         FROM erp_document_items
         WHERE doc_type = %L AND doc_created_at >= %L AND doc_created_at < %L',
        p_doc_type, p_start, p_end
    );

    -- 归档表 UNION（仅查归档区间，避免与热表重叠）
    IF need_archive THEN
        base_q := base_q || format(
            ' UNION ALL
             SELECT doc_id, quantity, amount, outer_id, item_name,
                    shop_name, platform, supplier_name, warehouse_name,
                    doc_status, order_status
             FROM erp_document_items_archive
             WHERE doc_type = %L AND doc_created_at >= %L AND doc_created_at < %L',
            p_doc_type, p_start, LEAST(p_end, NOW() - INTERVAL '90 days')
        );
    END IF;

    -- 可选筛选条件
    base_q := 'SELECT * FROM (' || base_q || ') AS raw WHERE 1=1';
    IF p_shop IS NOT NULL THEN
        base_q := base_q || format(' AND shop_name ILIKE %L', '%%' || p_shop || '%%');
    END IF;
    IF p_platform IS NOT NULL THEN
        base_q := base_q || format(' AND platform = %L', p_platform);
    END IF;
    IF p_supplier IS NOT NULL THEN
        base_q := base_q || format(' AND supplier_name ILIKE %L', '%%' || p_supplier || '%%');
    END IF;
    IF p_warehouse IS NOT NULL THEN
        base_q := base_q || format(' AND warehouse_name ILIKE %L', '%%' || p_warehouse || '%%');
    END IF;

    IF p_group_by IS NULL THEN
        -- 总计模式
        EXECUTE format(
            'SELECT jsonb_build_object(
                ''doc_count'', COUNT(DISTINCT doc_id),
                ''total_qty'', COALESCE(SUM(quantity), 0),
                ''total_amount'', COALESCE(SUM(amount), 0)
            ) FROM (%s) sub', base_q
        ) INTO result;
    ELSE
        -- 分组模式
        group_col := CASE p_group_by
            WHEN 'product' THEN 'outer_id'
            WHEN 'shop' THEN 'shop_name'
            WHEN 'platform' THEN 'platform'
            WHEN 'supplier' THEN 'supplier_name'
            WHEN 'warehouse' THEN 'warehouse_name'
            WHEN 'status' THEN 'COALESCE(doc_status, order_status)'
            ELSE 'doc_type'
        END;
        name_col := CASE p_group_by
            WHEN 'product' THEN ', MAX(item_name) as item_name'
            ELSE ''
        END;

        -- status 分组特殊处理（表达式不能用 %I）
        IF p_group_by = 'status' THEN
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
                 FROM (
                    SELECT COALESCE(doc_status, order_status, ''未知'') as group_key,
                           COUNT(DISTINCT doc_id) as doc_count,
                           COALESCE(SUM(quantity), 0) as total_qty,
                           COALESCE(SUM(amount), 0) as total_amount
                    FROM (%s) sub
                    GROUP BY COALESCE(doc_status, order_status, ''未知'')
                    ORDER BY COUNT(DISTINCT doc_id) DESC
                    LIMIT %s
                 ) t',
                base_q, p_limit
            ) INTO result;
        ELSE
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
                 FROM (
                    SELECT %I as group_key %s,
                           COUNT(DISTINCT doc_id) as doc_count,
                           COALESCE(SUM(quantity), 0) as total_qty,
                           COALESCE(SUM(amount), 0) as total_amount
                    FROM (%s) sub
                    WHERE %I IS NOT NULL
                    GROUP BY %I
                    ORDER BY COALESCE(SUM(amount), 0) DESC
                    LIMIT %s
                 ) t',
                group_col, name_col, base_q, group_col, group_col, p_limit
            ) INTO result;
        END IF;
    END IF;

    RETURN COALESCE(result, '{}'::jsonb);
END;
$$;

-- 056: 统一平台编码 + RPC group_by=shop 加 platform
--
-- 问题 1: erp_shops 存中文平台名（拼多多/京东/快手/小红书），
--         erp_document_items 存英文缩写（pdd/jd/kuaishou/xhs），
--         导致 local_shop_list 和 local_global_stats 的 platform 无法关联。
-- 问题 2: erp_global_stats_query group_by=shop 时只按 shop_name 分组，
--         同名跨平台店铺数据合并，且不返回 platform 字段。
--
-- 修复: 统一用英文编码 + RPC 返回 platform

-- ── Part 1: 统一 erp_shops 平台编码 ────────────────────

UPDATE erp_shops SET platform = 'pdd' WHERE platform = '拼多多';
UPDATE erp_shops SET platform = 'jd' WHERE platform = '京东';
UPDATE erp_shops SET platform = 'kuaishou' WHERE platform = '快手';
UPDATE erp_shops SET platform = 'xhs' WHERE platform = '小红书';
UPDATE erp_shops SET platform = 'tb' WHERE platform = '淘宝';
UPDATE erp_shops SET platform = 'tb' WHERE platform = '天猫';

-- ── Part 2: RPC group_by=shop 加 platform ──────────────

CREATE OR REPLACE FUNCTION erp_global_stats_query(
    p_doc_type VARCHAR,
    p_start TIMESTAMPTZ,
    p_end TIMESTAMPTZ,
    p_time_col VARCHAR DEFAULT 'doc_created_at',
    p_shop VARCHAR DEFAULT NULL,
    p_platform VARCHAR DEFAULT NULL,
    p_supplier VARCHAR DEFAULT NULL,
    p_warehouse VARCHAR DEFAULT NULL,
    p_group_by VARCHAR DEFAULT NULL,
    p_limit INT DEFAULT 20,
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    result JSONB;
    base_q TEXT;
    group_col TEXT;
    name_col TEXT;
    need_archive BOOLEAN;
    org_filter TEXT;
    time_col TEXT;
BEGIN
    IF p_start > p_end THEN
        RETURN jsonb_build_object('error', 'p_start must <= p_end');
    END IF;

    -- 白名单校验时间字段（防 SQL 注入）
    IF p_time_col IN ('doc_created_at', 'pay_time', 'consign_time') THEN
        time_col := p_time_col;
    ELSE
        time_col := 'doc_created_at';
    END IF;

    need_archive := (p_start < NOW() - INTERVAL '90 days');

    -- org_id 过滤条件
    IF p_org_id IS NOT NULL THEN
        org_filter := format(' AND org_id = %L', p_org_id);
    ELSE
        org_filter := ' AND org_id IS NULL';
    END IF;

    base_q := format(
        'SELECT doc_id, quantity, amount, outer_id, item_name,
                shop_name, platform, supplier_name, warehouse_name,
                doc_status, order_status
         FROM erp_document_items
         WHERE doc_type = %L AND %I >= %L AND %I < %L',
        p_doc_type, time_col, p_start, time_col, p_end
    ) || org_filter;

    IF need_archive THEN
        base_q := base_q || format(
            ' UNION ALL
             SELECT doc_id, quantity, amount, outer_id, item_name,
                    shop_name, platform, supplier_name, warehouse_name,
                    doc_status, order_status
             FROM erp_document_items_archive
             WHERE doc_type = %L AND %I >= %L AND %I < %L',
            p_doc_type, time_col, p_start, time_col, LEAST(p_end, NOW() - INTERVAL '90 days')
        ) || org_filter;
    END IF;

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
        EXECUTE format(
            'SELECT jsonb_build_object(
                ''doc_count'', COUNT(DISTINCT doc_id),
                ''total_qty'', COALESCE(SUM(quantity), 0),
                ''total_amount'', COALESCE(SUM(amount), 0)
            ) FROM (%s) sub', base_q
        ) INTO result;
    ELSIF p_group_by = 'shop' THEN
        -- 店铺分组：按 shop_name + platform 联合分组，避免跨平台同名合并
        EXECUTE format(
            'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
             FROM (
                SELECT shop_name as group_key, platform,
                       COUNT(DISTINCT doc_id) as doc_count,
                       COALESCE(SUM(quantity), 0) as total_qty,
                       COALESCE(SUM(amount), 0) as total_amount
                FROM (%s) sub
                WHERE shop_name IS NOT NULL
                GROUP BY shop_name, platform
                ORDER BY COALESCE(SUM(amount), 0) DESC
                LIMIT %s
             ) t',
            base_q, p_limit
        ) INTO result;
    ELSIF p_group_by = 'status' THEN
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
        group_col := CASE p_group_by
            WHEN 'product' THEN 'outer_id'
            WHEN 'platform' THEN 'platform'
            WHEN 'supplier' THEN 'supplier_name'
            WHEN 'warehouse' THEN 'warehouse_name'
            ELSE 'doc_type'
        END;
        name_col := CASE p_group_by
            WHEN 'product' THEN ', MAX(item_name) as item_name'
            ELSE ''
        END;

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

    RETURN COALESCE(result, '{}'::jsonb);
END;
$$;

COMMENT ON FUNCTION erp_global_stats_query IS 'ERP全局统计RPC（多租户），group_by=shop 按 shop_name+platform 联合分组';

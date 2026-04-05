-- ============================================================
-- 041: erp_global_stats_query 支持动态时间字段
--
-- 新增 p_time_col 参数，支持按 doc_created_at / pay_time / consign_time 统计。
-- 解决"今天付款了多少订单"按 doc_created_at 统计导致数据不准的问题。
-- ============================================================

-- 为 pay_time 和 consign_time 添加索引（加速按付款/发货时间的全局统计）
CREATE INDEX IF NOT EXISTS idx_doc_items_pay_time
  ON erp_document_items (doc_type, pay_time DESC)
  WHERE pay_time IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_doc_items_consign_time
  ON erp_document_items (doc_type, consign_time DESC)
  WHERE consign_time IS NOT NULL;

-- 重建 RPC：加 p_time_col 参数
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
    ELSE
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

COMMENT ON FUNCTION erp_global_stats_query IS 'ERP全局统计RPC（多租户），支持 p_time_col 动态时间字段';

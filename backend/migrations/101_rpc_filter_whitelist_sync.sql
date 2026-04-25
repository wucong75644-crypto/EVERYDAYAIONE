-- 101: RPC filter 白名单同步 — 修复 erp_order_stats_grouped 维度字段过滤失效
--
-- 根因: erp_order_stats_grouped 的 DSL filter 白名单缺少 platform/shop_name/
--       supplier_name/warehouse_name/item_name，导致这些过滤条件被 CONTINUE 静默跳过。
--       Python 端 validate_filters 用 COLUMN_WHITELIST 校验通过，但 RPC 端又丢了。
--
-- 修复: 白名单扩展为 base_q SELECT 全列（除 doc_id/org_id），
--       与 Python COLUMN_WHITELIST 子集对齐，消除两层白名单漂移。
--
-- 影响: erp_order_stats_grouped（分类统计 RPC）
--       erp_global_stats_query 不受影响（维度走命名参数 p_platform 等）
--
-- 同步规则: 白名单 = base_q SELECT 列 - {doc_id, org_id}
--          新增列到 base_q 时必须同步加入白名单。

-- ── 重建 erp_order_stats_grouped ─────────────────────

DROP FUNCTION IF EXISTS erp_order_stats_grouped(UUID, TIMESTAMPTZ, TIMESTAMPTZ, VARCHAR, JSONB, VARCHAR);

CREATE OR REPLACE FUNCTION erp_order_stats_grouped(
    p_org_id UUID,
    p_start TIMESTAMPTZ,
    p_end TIMESTAMPTZ,
    p_time_col VARCHAR DEFAULT 'pay_time',
    p_filters JSONB DEFAULT NULL,
    p_group_by VARCHAR DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    base_q TEXT;
    result JSONB;
    time_col TEXT;
    need_archive BOOLEAN;
    group_col TEXT;
    i INT;
    f JSONB;
    field_name TEXT;
    op TEXT;
    val JSONB;
    val_text TEXT;
BEGIN
    IF p_start > p_end THEN
        RETURN '[]'::jsonb;
    END IF;

    IF p_time_col IN ('doc_created_at', 'pay_time', 'consign_time') THEN
        time_col := p_time_col;
    ELSE
        time_col := 'pay_time';
    END IF;

    need_archive := (p_start < NOW() - INTERVAL '90 days');

    base_q := format(
        'SELECT doc_id, quantity, amount, outer_id, item_name, '
        'shop_name, shop_user_id, platform, supplier_name, supplier_code, '
        'warehouse_name, warehouse_id, '
        'doc_status, order_status, order_type, order_no, '
        'sku_outer_id, express_no, buyer_nick, status_name, '
        'cost, pay_amount, gross_profit, refund_money, '
        'post_fee, discount_fee, aftersale_type, refund_status, '
        'is_cancel, is_refund, is_exception, is_halt, is_urgent, '
        'is_scalping, unified_status, is_presell, '
        'online_status, handler_status, org_id '
        'FROM erp_document_items '
        'WHERE doc_type = ''order'' '
        'AND org_id = %L '
        'AND %I >= %L AND %I < %L',
        p_org_id, time_col, p_start, time_col, p_end
    );

    IF need_archive THEN
        base_q := base_q || format(
            ' UNION ALL '
            'SELECT doc_id, quantity, amount, outer_id, item_name, '
            'shop_name, shop_user_id, platform, supplier_name, supplier_code, '
            'warehouse_name, warehouse_id, '
            'doc_status, order_status, order_type, order_no, '
            'sku_outer_id, express_no, buyer_nick, status_name, '
            'cost, pay_amount, gross_profit, refund_money, '
            'post_fee, discount_fee, aftersale_type, refund_status, '
            'is_cancel, is_refund, is_exception, is_halt, is_urgent, '
            'is_scalping, unified_status, is_presell, '
            'online_status, handler_status, org_id '
            'FROM erp_document_items_archive '
            'WHERE doc_type = ''order'' '
            'AND org_id = %L '
            'AND %I >= %L AND %I < %L',
            p_org_id, time_col, p_start, time_col, LEAST(p_end, NOW() - INTERVAL '90 days')
        );
    END IF;

    base_q := 'SELECT * FROM (' || base_q || ') AS raw WHERE 1=1';

    -- ── Filter DSL 解析 ─────────────────────────────────────
    -- WHITELIST_SYNC: 必须与 base_q SELECT 列保持一致（减 doc_id/org_id）
    -- Python 端对照常量: erp_unified_schema.RPC_ORDER_STATS_FILTER_FIELDS
    IF p_filters IS NOT NULL AND jsonb_typeof(p_filters) = 'array' THEN
        FOR i IN 0..jsonb_array_length(p_filters) - 1 LOOP
            f := p_filters->i;
            field_name := f->>'field';
            op := f->>'op';
            val := f->'value';
            val_text := val#>>'{}';

            IF field_name NOT IN (
                -- 维度字段（101 新增 ★）
                'platform', 'shop_name', 'supplier_name', 'warehouse_name', 'item_name',
                -- 关联 ID（100 已有）
                'supplier_code', 'shop_user_id', 'warehouse_id',
                -- 文本字段
                'order_status', 'doc_status', 'status_name',
                'outer_id', 'sku_outer_id', 'order_no', 'express_no',
                'buyer_nick', 'order_type',
                'aftersale_type', 'refund_status',
                -- 数值字段
                'amount', 'quantity', 'cost', 'pay_amount',
                'gross_profit', 'refund_money', 'post_fee', 'discount_fee',
                -- 标记字段
                'is_cancel', 'is_refund', 'is_exception', 'is_halt', 'is_urgent',
                'is_scalping', 'unified_status', 'is_presell',
                'online_status', 'handler_status'
            ) THEN
                CONTINUE;
            END IF;

            CASE op
                WHEN 'eq' THEN
                    base_q := base_q || format(' AND %I = %L', field_name, val_text);
                WHEN 'ne' THEN
                    base_q := base_q || format(' AND %I != %L', field_name, val_text);
                WHEN 'gt' THEN
                    base_q := base_q || format(' AND %I > %L', field_name, val_text);
                WHEN 'gte' THEN
                    base_q := base_q || format(' AND %I >= %L', field_name, val_text);
                WHEN 'lt' THEN
                    base_q := base_q || format(' AND %I < %L', field_name, val_text);
                WHEN 'lte' THEN
                    base_q := base_q || format(' AND %I <= %L', field_name, val_text);
                WHEN 'like' THEN
                    base_q := base_q || format(' AND %I ILIKE %L', field_name, val_text);
                WHEN 'not_like' THEN
                    base_q := base_q || format(' AND %I NOT ILIKE %L', field_name, val_text);
                WHEN 'in' THEN
                    IF jsonb_typeof(val) = 'array' AND jsonb_array_length(val) > 0 THEN
                        base_q := base_q || format(
                            ' AND %I IN (SELECT jsonb_array_elements_text(%L::jsonb))',
                            field_name, val::text
                        );
                    END IF;
                WHEN 'is_null' THEN
                    IF val_text = 'true' OR val_text = '1' THEN
                        base_q := base_q || format(' AND %I IS NULL', field_name);
                    ELSE
                        base_q := base_q || format(' AND %I IS NOT NULL', field_name);
                    END IF;
                WHEN 'between' THEN
                    IF jsonb_typeof(val) = 'array' AND jsonb_array_length(val) = 2 THEN
                        base_q := base_q || format(
                            ' AND %I BETWEEN %L AND %L',
                            field_name, val->>0, val->>1
                        );
                    END IF;
                ELSE
                    NULL;
            END CASE;
        END LOOP;
    END IF;

    -- ── 聚合 ──
    IF p_group_by IS NULL THEN
        EXECUTE format(
            'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
            'FROM ('
            '  SELECT order_type, order_status, is_scalping, '
            '         COUNT(DISTINCT doc_id) AS doc_count, '
            '         COALESCE(SUM(quantity), 0) AS total_qty, '
            '         COALESCE(SUM(amount), 0) AS total_amount '
            '  FROM (%s) sub '
            '  GROUP BY order_type, order_status, is_scalping'
            ') t',
            base_q
        ) INTO result;
    ELSE
        IF p_group_by = 'status' THEN
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
                'FROM ('
                '  SELECT COALESCE(doc_status, order_status, ''未知'') AS group_key, '
                '         order_type, order_status, is_scalping, '
                '         COUNT(DISTINCT doc_id) AS doc_count, '
                '         COALESCE(SUM(quantity), 0) AS total_qty, '
                '         COALESCE(SUM(amount), 0) AS total_amount '
                '  FROM (%s) sub '
                '  GROUP BY COALESCE(doc_status, order_status, ''未知''), '
                '           order_type, order_status, is_scalping '
                '  ORDER BY COUNT(DISTINCT doc_id) DESC'
                ') t',
                base_q
            ) INTO result;

        ELSIF p_group_by = 'product' THEN
            -- 100: item_name 兜底 erp_products.title
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
                'FROM ('
                '  SELECT sub.outer_id AS group_key, '
                '         COALESCE(MAX(sub.item_name), (SELECT ep.title FROM erp_products ep WHERE ep.outer_id = sub.outer_id AND ep.org_id = %L LIMIT 1)) AS item_name, '
                '         order_type, order_status, is_scalping, '
                '         COUNT(DISTINCT sub.doc_id) AS doc_count, '
                '         COALESCE(SUM(sub.quantity), 0) AS total_qty, '
                '         COALESCE(SUM(sub.amount), 0) AS total_amount '
                '  FROM (%s) sub '
                '  WHERE sub.outer_id IS NOT NULL '
                '  GROUP BY sub.outer_id, order_type, order_status, is_scalping '
                '  ORDER BY COALESCE(SUM(sub.amount), 0) DESC'
                ') t',
                p_org_id, base_q
            ) INTO result;

        ELSIF p_group_by = 'shop' THEN
            -- 100: shop_user_id + LEFT JOIN erp_shops
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
                'FROM ('
                '  SELECT COALESCE(dim.name, sub.shop_name) AS group_key, '
                '         order_type, order_status, is_scalping, '
                '         COUNT(DISTINCT sub.doc_id) AS doc_count, '
                '         COALESCE(SUM(sub.quantity), 0) AS total_qty, '
                '         COALESCE(SUM(sub.amount), 0) AS total_amount '
                '  FROM (%s) sub '
                '  LEFT JOIN erp_shops dim ON sub.shop_user_id = dim.user_id AND sub.org_id = dim.org_id '
                '  WHERE sub.shop_user_id IS NOT NULL OR sub.shop_name IS NOT NULL '
                '  GROUP BY COALESCE(dim.name, sub.shop_name), order_type, order_status, is_scalping '
                '  ORDER BY COALESCE(SUM(sub.amount), 0) DESC'
                ') t',
                base_q
            ) INTO result;

        ELSIF p_group_by = 'supplier' THEN
            -- 100: supplier_code + LEFT JOIN erp_suppliers
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
                'FROM ('
                '  SELECT COALESCE(dim.name, sub.supplier_name) AS group_key, '
                '         order_type, order_status, is_scalping, '
                '         COUNT(DISTINCT sub.doc_id) AS doc_count, '
                '         COALESCE(SUM(sub.quantity), 0) AS total_qty, '
                '         COALESCE(SUM(sub.amount), 0) AS total_amount '
                '  FROM (%s) sub '
                '  LEFT JOIN erp_suppliers dim ON sub.supplier_code = dim.code AND sub.org_id = dim.org_id '
                '  WHERE sub.supplier_code IS NOT NULL OR sub.supplier_name IS NOT NULL '
                '  GROUP BY COALESCE(dim.name, sub.supplier_name), order_type, order_status, is_scalping '
                '  ORDER BY COALESCE(SUM(sub.amount), 0) DESC'
                ') t',
                base_q
            ) INTO result;

        ELSIF p_group_by = 'warehouse' THEN
            -- 100: warehouse_id + LEFT JOIN erp_warehouses
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
                'FROM ('
                '  SELECT COALESCE(dim.name, sub.warehouse_name) AS group_key, '
                '         order_type, order_status, is_scalping, '
                '         COUNT(DISTINCT sub.doc_id) AS doc_count, '
                '         COALESCE(SUM(sub.quantity), 0) AS total_qty, '
                '         COALESCE(SUM(sub.amount), 0) AS total_amount '
                '  FROM (%s) sub '
                '  LEFT JOIN erp_warehouses dim ON sub.warehouse_id = dim.warehouse_id AND sub.org_id = dim.org_id '
                '  WHERE sub.warehouse_id IS NOT NULL OR sub.warehouse_name IS NOT NULL '
                '  GROUP BY COALESCE(dim.name, sub.warehouse_name), order_type, order_status, is_scalping '
                '  ORDER BY COALESCE(SUM(sub.amount), 0) DESC'
                ') t',
                base_q
            ) INTO result;

        ELSE
            -- platform 等：直接用字段值分组
            group_col := CASE p_group_by
                WHEN 'platform' THEN 'platform'
                ELSE p_group_by
            END;
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
                'FROM ('
                '  SELECT %I AS group_key, '
                '         order_type, order_status, is_scalping, '
                '         COUNT(DISTINCT doc_id) AS doc_count, '
                '         COALESCE(SUM(quantity), 0) AS total_qty, '
                '         COALESCE(SUM(amount), 0) AS total_amount '
                '  FROM (%s) sub '
                '  WHERE %I IS NOT NULL '
                '  GROUP BY %I, order_type, order_status, is_scalping '
                '  ORDER BY COALESCE(SUM(amount), 0) DESC'
                ') t',
                group_col, base_q, group_col, group_col
            ) INTO result;
        END IF;
    END IF;

    RETURN COALESCE(result, '[]'::jsonb);
END;
$$;

COMMENT ON FUNCTION erp_order_stats_grouped IS
    '订单分类统计RPC — 101: filter 白名单补全维度字段(platform/shop_name/supplier_name/warehouse_name/item_name)';

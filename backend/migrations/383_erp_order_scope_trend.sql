-- Extend the existing classification RPC with an explicit seven-argument trend overload.
-- Existing six-argument summary/compare callers and grants remain unchanged.
CREATE OR REPLACE FUNCTION erp_order_stats_grouped(
    p_org_id UUID,
    p_start TIMESTAMPTZ,
    p_end TIMESTAMPTZ,
    p_time_col VARCHAR,
    p_filters JSONB,
    p_group_by VARCHAR,
    p_time_granularity VARCHAR
) RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    base_q TEXT;
    result JSONB;
    time_col TEXT;
    need_archive BOOLEAN;
    group_col TEXT;
    dimension_expr TEXT;
    i INT;
    f JSONB;
    field_name TEXT;
    op TEXT;
    val JSONB;
    val_text TEXT;
BEGIN
    IF p_time_granularity NOT IN ('day', 'week', 'month') OR p_time_granularity IS NULL THEN
        RAISE EXCEPTION 'Unsupported order trend granularity';
    END IF;
    IF p_time_col NOT IN ('doc_created_at', 'pay_time', 'consign_time') OR p_time_col IS NULL THEN
        RAISE EXCEPTION 'Unsupported order trend time column';
    END IF;
    IF p_group_by IS NOT NULL AND p_group_by NOT IN ('status','product','shop','supplier','warehouse','platform') THEN
        RAISE EXCEPTION 'Unsupported order trend dimension';
    END IF;
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
        'SELECT %I AS trend_time, pay_time, doc_created_at, consign_time, doc_id, quantity, amount, outer_id, item_name, '
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
        time_col, p_org_id, time_col, p_start, time_col, p_end
    );

    IF need_archive THEN
        base_q := base_q || format(
            ' UNION ALL '
            'SELECT %I AS trend_time, pay_time, doc_created_at, consign_time, doc_id, quantity, amount, outer_id, item_name, '
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
            time_col, p_org_id, time_col, p_start, time_col, LEAST(p_end, NOW() - INTERVAL '90 days')
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
                'doc_created_at', 'pay_time', 'consign_time',
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
                RAISE EXCEPTION 'Unsupported order trend filter field: %', field_name;
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
                            ' AND %I IN (%s)',
                            field_name, (SELECT string_agg(quote_nullable(v #>> '{}'), ',') FROM jsonb_array_elements(val) v)
                        );
                    END IF;
                WHEN 'not_in' THEN
                    IF jsonb_typeof(val) = 'array' AND jsonb_array_length(val) > 0 THEN
                        base_q := base_q || format(
                            ' AND %I NOT IN (%s)',
                            field_name, (SELECT string_agg(quote_nullable(v #>> '{}'), ',') FROM jsonb_array_elements(val) v)
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
                    RAISE EXCEPTION 'Unsupported order trend filter operator: %', op;
            END CASE;
        END LOOP;
    END IF;

    -- 聚合
    dimension_expr := CASE p_group_by
        WHEN 'status' THEN 'COALESCE(sub.doc_status, sub.order_status, ''未知'')'
        WHEN 'product' THEN 'sub.outer_id'
        WHEN 'platform' THEN 'sub.platform'
        WHEN 'shop' THEN 'COALESCE((SELECT d.name FROM erp_shops d WHERE d.user_id=sub.shop_user_id AND d.org_id=sub.org_id LIMIT 1), sub.shop_name)'
        WHEN 'supplier' THEN 'COALESCE((SELECT d.name FROM erp_suppliers d WHERE d.code=sub.supplier_code AND d.org_id=sub.org_id LIMIT 1), sub.supplier_name)'
        WHEN 'warehouse' THEN 'COALESCE((SELECT d.name FROM erp_warehouses d WHERE d.warehouse_id=sub.warehouse_id AND d.org_id=sub.org_id LIMIT 1), sub.warehouse_name)'
        ELSE 'NULL::text'
    END;
    EXECUTE format(
        'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) FROM ('
        'SELECT date_trunc(%L, sub.trend_time AT TIME ZONE ''Asia/Shanghai'')::date AS period, '
        '%s AS group_key, order_type, order_status, is_scalping, '
        'COUNT(DISTINCT doc_id) AS doc_count, '
        'COALESCE(SUM(quantity),0) AS total_qty, COALESCE(SUM(amount),0) AS total_amount '
        'FROM (%s) sub '
        'GROUP BY 1,2,order_type,order_status,is_scalping ORDER BY 1,2) t',
        p_time_granularity, dimension_expr, base_q
    ) INTO result;
    RETURN COALESCE(result, '[]'::jsonb);
END;
$$;
COMMENT ON FUNCTION erp_order_stats_grouped(UUID,TIMESTAMPTZ,TIMESTAMPTZ,VARCHAR,JSONB,VARCHAR,VARCHAR)
    IS 'Scoped order trend: tenant-filtered detail aggregation, Shanghai day/week/month; legacy six-argument RPC unchanged';

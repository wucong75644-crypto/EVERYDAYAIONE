-- 094: 采购类单据 item_name 兜底 — 从 erp_products.title 回填
--
-- 根因：快麦采购类 API（purchase.order.get / warehouse.entry.list.get /
--       purchase.return.list.get / erp.purchase.shelf.get）不返回 title 字段，
--       导致 erp_document_items.item_name 全部为 NULL（2550 条采购记录 100% 为空）。
--       订单 API（erp.trade.list.query）正常返回 title，不受影响。
--
-- 修复策略：
--   1. RPC 聚合层：group_by='product' 时，用 COALESCE(MAX(item_name), 子查询erp_products.title)
--   2. 历史数据：一次性 UPDATE 回填已有采购记录的 item_name

-- ── 1. 更新 erp_global_stats_query：product 分组时兜底取 erp_products.title ──

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
    p_org_id UUID DEFAULT NULL,
    p_filters JSONB DEFAULT NULL
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
    i INT;
    f JSONB;
    field_name TEXT;
    op TEXT;
    val JSONB;
    val_text TEXT;
BEGIN
    IF p_start > p_end THEN
        RETURN jsonb_build_object('error', 'p_start must <= p_end');
    END IF;

    IF p_time_col IN ('doc_created_at', 'pay_time', 'consign_time',
                      'apply_date', 'delivery_date', 'finished_at') THEN
        time_col := p_time_col;
    ELSE
        time_col := 'doc_created_at';
    END IF;

    need_archive := (p_start < NOW() - INTERVAL '90 days');

    IF p_org_id IS NOT NULL THEN
        org_filter := format(' AND org_id = %L', p_org_id);
    ELSE
        org_filter := ' AND org_id IS NULL';
    END IF;

    base_q := format(
        'SELECT * FROM erp_document_items
         WHERE doc_type = %L AND %I >= %L AND %I < %L',
        p_doc_type, time_col, p_start, time_col, p_end
    ) || org_filter;

    IF need_archive THEN
        base_q := base_q || format(
            ' UNION ALL
             SELECT * FROM erp_document_items_archive
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

    -- Filter DSL 解析
    IF p_filters IS NOT NULL AND jsonb_typeof(p_filters) = 'array' THEN
        FOR i IN 0..jsonb_array_length(p_filters) - 1 LOOP
            f := p_filters->i;
            field_name := f->>'field';
            op := f->>'op';
            val := f->'value';
            val_text := val#>>'{}';

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
                        base_q := base_q || format(' AND %I BETWEEN %L AND %L',
                            field_name, val->>0, val->>1);
                    END IF;
                ELSE
                    NULL;
            END CASE;
        END LOOP;
    END IF;

    -- 聚合逻辑
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
        -- 094: item_name 兜底 erp_products.title（采购类 API 不返回 title）
        name_col := CASE p_group_by
            WHEN 'product' THEN ', COALESCE(MAX(item_name), (SELECT ep.title FROM erp_products ep WHERE ep.outer_id = sub.outer_id AND ep.org_id' || COALESCE(' = ' || quote_literal(p_org_id), ' IS NULL') || ' LIMIT 1)) as item_name'
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

COMMENT ON FUNCTION erp_global_stats_query IS
    'ERP全局统计RPC — 094: product分组时item_name兜底erp_products.title';

-- ── 2. 更新 erp_order_stats_grouped：product 分组时兜底取 erp_products.title ──

DROP FUNCTION IF EXISTS erp_order_stats_grouped(UUID, TIMESTAMPTZ, TIMESTAMPTZ, VARCHAR, JSONB);

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
        'shop_name, platform, supplier_name, warehouse_name, '
        'doc_status, order_status, order_type, order_no, '
        'sku_outer_id, express_no, buyer_nick, status_name, '
        'cost, pay_amount, gross_profit, refund_money, '
        'post_fee, discount_fee, aftersale_type, refund_status, '
        'is_cancel, is_refund, is_exception, is_halt, is_urgent, '
        'is_scalping, unified_status, is_presell, '
        'online_status, handler_status '
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
            'shop_name, platform, supplier_name, warehouse_name, '
            'doc_status, order_status, order_type, order_no, '
            'sku_outer_id, express_no, buyer_nick, status_name, '
            'cost, pay_amount, gross_profit, refund_money, '
            'post_fee, discount_fee, aftersale_type, refund_status, '
            'is_cancel, is_refund, is_exception, is_halt, is_urgent, '
            'is_scalping, unified_status, is_presell, '
            'online_status, handler_status '
            'FROM erp_document_items_archive '
            'WHERE doc_type = ''order'' '
            'AND org_id = %L '
            'AND %I >= %L AND %I < %L',
            p_org_id, time_col, p_start, time_col, LEAST(p_end, NOW() - INTERVAL '90 days')
        );
    END IF;

    base_q := 'SELECT * FROM (' || base_q || ') AS raw WHERE 1=1';

    -- Filter DSL 解析
    IF p_filters IS NOT NULL AND jsonb_typeof(p_filters) = 'array' THEN
        FOR i IN 0..jsonb_array_length(p_filters) - 1 LOOP
            f := p_filters->i;
            field_name := f->>'field';
            op := f->>'op';
            val := f->'value';
            val_text := val#>>'{}';

            IF field_name NOT IN (
                'order_status', 'doc_status', 'status_name',
                'outer_id', 'sku_outer_id', 'order_no', 'express_no',
                'buyer_nick', 'order_type',
                'aftersale_type', 'refund_status',
                'amount', 'quantity', 'cost', 'pay_amount',
                'gross_profit', 'refund_money', 'post_fee', 'discount_fee',
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

    -- 聚合
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
        group_col := CASE p_group_by
            WHEN 'product' THEN 'outer_id'
            WHEN 'shop' THEN 'shop_name'
            WHEN 'platform' THEN 'platform'
            WHEN 'supplier' THEN 'supplier_name'
            WHEN 'warehouse' THEN 'warehouse_name'
            WHEN 'status' THEN 'COALESCE(doc_status, order_status)'
            ELSE p_group_by
        END;

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
            -- 094: item_name 兜底 erp_products.title（采购类 API 不返回 title）
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
                'FROM ('
                '  SELECT %I AS group_key, '
                '         COALESCE(MAX(item_name), (SELECT ep.title FROM erp_products ep WHERE ep.outer_id = sub.outer_id AND ep.org_id = %L LIMIT 1)) AS item_name, '
                '         order_type, order_status, is_scalping, '
                '         COUNT(DISTINCT doc_id) AS doc_count, '
                '         COALESCE(SUM(quantity), 0) AS total_qty, '
                '         COALESCE(SUM(amount), 0) AS total_amount '
                '  FROM (%s) sub '
                '  WHERE %I IS NOT NULL '
                '  GROUP BY %I, order_type, order_status, is_scalping '
                '  ORDER BY COALESCE(SUM(amount), 0) DESC'
                ') t',
                group_col, p_org_id, base_q, group_col, group_col
            ) INTO result;
        ELSE
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
    '订单分类统计RPC — 094: product分组时item_name兜底erp_products.title';

-- ── 3. 一次性回填历史采购记录的 item_name ──

UPDATE erp_document_items di
SET item_name = ep.title
FROM erp_products ep
WHERE di.item_name IS NULL
  AND di.outer_id IS NOT NULL
  AND di.outer_id = ep.outer_id
  AND COALESCE(di.org_id, '00000000-0000-0000-0000-000000000000') = COALESCE(ep.org_id, '00000000-0000-0000-0000-000000000000')
  AND di.doc_type IN ('purchase', 'receipt', 'shelf', 'purchase_return');

-- 同样回填归档表
UPDATE erp_document_items_archive dia
SET item_name = ep.title
FROM erp_products ep
WHERE dia.item_name IS NULL
  AND dia.outer_id IS NOT NULL
  AND dia.outer_id = ep.outer_id
  AND COALESCE(dia.org_id, '00000000-0000-0000-0000-000000000000') = COALESCE(ep.org_id, '00000000-0000-0000-0000-000000000000')
  AND dia.doc_type IN ('purchase', 'receipt', 'shelf', 'purchase_return');

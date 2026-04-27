-- 106: erp_global_stats_query 扩展 — AVG/MIN/MAX + 多维 GROUP BY + buyer_nick
--
-- 扩展点：
--   1. 非分组汇总：新增 avg_amount / min_amount / max_amount / distinct_buyer / total_cost / total_profit
--   2. 分组汇总：每个分组分支新增同样的聚合列
--   3. 签名不变，向后兼容（新列是追加的，旧代码读不到就忽略）
--
-- 设计文档: docs/document/TECH_ERP查询架构重构.md §5.2
-- 依赖: 104_rpc_add_not_in_op.sql（最新版基线）

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

    -- Filter DSL 解析（与 104 完全一致）
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
                WHEN 'not_in' THEN
                    IF jsonb_typeof(val) = 'array' AND jsonb_array_length(val) > 0 THEN
                        base_q := base_q || format(
                            ' AND %I NOT IN (SELECT jsonb_array_elements_text(%L::jsonb))',
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

    -- ── 聚合逻辑（106 扩展：新增 avg/min/max/distinct_buyer/cost/profit）──

    IF p_group_by IS NULL THEN
        EXECUTE format(
            'SELECT jsonb_build_object(
                ''doc_count'', COUNT(DISTINCT doc_id),
                ''total_qty'', COALESCE(SUM(quantity), 0),
                ''total_amount'', COALESCE(SUM(amount), 0),
                ''avg_amount'', ROUND(COALESCE(AVG(amount), 0)::numeric, 2),
                ''min_amount'', COALESCE(MIN(amount), 0),
                ''max_amount'', COALESCE(MAX(amount), 0),
                ''distinct_buyer'', COUNT(DISTINCT CASE WHEN buyer_nick IS NOT NULL AND buyer_nick != '''' THEN buyer_nick END),
                ''total_cost'', COALESCE(SUM(cost), 0),
                ''total_profit'', COALESCE(SUM(gross_profit), 0)
            ) FROM (%s) sub', base_q
        ) INTO result;
    ELSE
        -- 106: 每个分组分支新增扩展聚合列
        IF p_group_by = 'status' THEN
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
                 FROM (
                    SELECT COALESCE(doc_status, order_status, ''未知'') as group_key,
                           COUNT(DISTINCT doc_id) as doc_count,
                           COALESCE(SUM(quantity), 0) as total_qty,
                           COALESCE(SUM(amount), 0) as total_amount,
                           ROUND(COALESCE(AVG(amount), 0)::numeric, 2) as avg_amount,
                           COALESCE(MIN(amount), 0) as min_amount,
                           COALESCE(MAX(amount), 0) as max_amount,
                           COUNT(DISTINCT CASE WHEN buyer_nick IS NOT NULL AND buyer_nick != '''' THEN buyer_nick END) as distinct_buyer,
                           COALESCE(SUM(cost), 0) as total_cost,
                           COALESCE(SUM(gross_profit), 0) as total_profit
                    FROM (%s) sub
                    GROUP BY COALESCE(doc_status, order_status, ''未知'')
                    ORDER BY COALESCE(SUM(amount), 0) DESC
                    LIMIT %s
                 ) t',
                base_q, p_limit
            ) INTO result;

        ELSIF p_group_by = 'product' THEN
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
                 FROM (
                    SELECT sub.outer_id as group_key,
                           COALESCE(MAX(sub.item_name), (SELECT ep.title FROM erp_products ep WHERE ep.outer_id = sub.outer_id AND ep.org_id' || COALESCE(' = ' || quote_literal(p_org_id), ' IS NULL') || ' LIMIT 1)) as item_name,
                           COUNT(DISTINCT sub.doc_id) as doc_count,
                           COALESCE(SUM(sub.quantity), 0) as total_qty,
                           COALESCE(SUM(sub.amount), 0) as total_amount,
                           ROUND(COALESCE(AVG(sub.amount), 0)::numeric, 2) as avg_amount,
                           COALESCE(MIN(sub.amount), 0) as min_amount,
                           COALESCE(MAX(sub.amount), 0) as max_amount,
                           COUNT(DISTINCT CASE WHEN sub.buyer_nick IS NOT NULL AND sub.buyer_nick != '''' THEN sub.buyer_nick END) as distinct_buyer,
                           COALESCE(SUM(sub.cost), 0) as total_cost,
                           COALESCE(SUM(sub.gross_profit), 0) as total_profit
                    FROM (%s) sub
                    WHERE sub.outer_id IS NOT NULL
                    GROUP BY sub.outer_id
                    ORDER BY COALESCE(SUM(sub.amount), 0) DESC
                    LIMIT %s
                 ) t',
                base_q, p_limit
            ) INTO result;

        ELSIF p_group_by = 'shop' THEN
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
                 FROM (
                    SELECT COALESCE(dim.name, sub.shop_name) as group_key,
                           COUNT(DISTINCT sub.doc_id) as doc_count,
                           COALESCE(SUM(sub.quantity), 0) as total_qty,
                           COALESCE(SUM(sub.amount), 0) as total_amount,
                           ROUND(COALESCE(AVG(sub.amount), 0)::numeric, 2) as avg_amount,
                           COALESCE(MIN(sub.amount), 0) as min_amount,
                           COALESCE(MAX(sub.amount), 0) as max_amount,
                           COUNT(DISTINCT CASE WHEN sub.buyer_nick IS NOT NULL AND sub.buyer_nick != '''' THEN sub.buyer_nick END) as distinct_buyer,
                           COALESCE(SUM(sub.cost), 0) as total_cost,
                           COALESCE(SUM(sub.gross_profit), 0) as total_profit
                    FROM (%s) sub
                    LEFT JOIN erp_shops dim ON sub.shop_user_id = dim.user_id
                        AND sub.org_id = dim.org_id
                    WHERE sub.shop_user_id IS NOT NULL OR sub.shop_name IS NOT NULL
                    GROUP BY COALESCE(dim.name, sub.shop_name)
                    ORDER BY COALESCE(SUM(sub.amount), 0) DESC
                    LIMIT %s
                 ) t',
                base_q, p_limit
            ) INTO result;

        ELSIF p_group_by = 'supplier' THEN
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
                 FROM (
                    SELECT COALESCE(dim.name, sub.supplier_name) as group_key,
                           COUNT(DISTINCT sub.doc_id) as doc_count,
                           COALESCE(SUM(sub.quantity), 0) as total_qty,
                           COALESCE(SUM(sub.amount), 0) as total_amount,
                           ROUND(COALESCE(AVG(sub.amount), 0)::numeric, 2) as avg_amount,
                           COALESCE(MIN(sub.amount), 0) as min_amount,
                           COALESCE(MAX(sub.amount), 0) as max_amount,
                           COUNT(DISTINCT CASE WHEN sub.buyer_nick IS NOT NULL AND sub.buyer_nick != '''' THEN sub.buyer_nick END) as distinct_buyer,
                           COALESCE(SUM(sub.cost), 0) as total_cost,
                           COALESCE(SUM(sub.gross_profit), 0) as total_profit
                    FROM (%s) sub
                    LEFT JOIN erp_suppliers dim ON sub.supplier_code = dim.code
                        AND sub.org_id = dim.org_id
                    WHERE sub.supplier_code IS NOT NULL OR sub.supplier_name IS NOT NULL
                    GROUP BY COALESCE(dim.name, sub.supplier_name)
                    ORDER BY COALESCE(SUM(sub.amount), 0) DESC
                    LIMIT %s
                 ) t',
                base_q, p_limit
            ) INTO result;

        ELSIF p_group_by = 'warehouse' THEN
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
                 FROM (
                    SELECT COALESCE(dim.name, sub.warehouse_name) as group_key,
                           COUNT(DISTINCT sub.doc_id) as doc_count,
                           COALESCE(SUM(sub.quantity), 0) as total_qty,
                           COALESCE(SUM(sub.amount), 0) as total_amount,
                           ROUND(COALESCE(AVG(sub.amount), 0)::numeric, 2) as avg_amount,
                           COALESCE(MIN(sub.amount), 0) as min_amount,
                           COALESCE(MAX(sub.amount), 0) as max_amount,
                           COUNT(DISTINCT CASE WHEN sub.buyer_nick IS NOT NULL AND sub.buyer_nick != '''' THEN sub.buyer_nick END) as distinct_buyer,
                           COALESCE(SUM(sub.cost), 0) as total_cost,
                           COALESCE(SUM(sub.gross_profit), 0) as total_profit
                    FROM (%s) sub
                    LEFT JOIN erp_warehouses dim ON sub.warehouse_id = dim.warehouse_id
                        AND sub.org_id = dim.org_id
                    WHERE sub.warehouse_id IS NOT NULL OR sub.warehouse_name IS NOT NULL
                    GROUP BY COALESCE(dim.name, sub.warehouse_name)
                    ORDER BY COALESCE(SUM(sub.amount), 0) DESC
                    LIMIT %s
                 ) t',
                base_q, p_limit
            ) INTO result;

        ELSE
            -- platform / doc_type 等：直接用字段值分组
            group_col := CASE p_group_by
                WHEN 'platform' THEN 'platform'
                ELSE 'doc_type'
            END;
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb)
                 FROM (
                    SELECT %I as group_key,
                           COUNT(DISTINCT doc_id) as doc_count,
                           COALESCE(SUM(quantity), 0) as total_qty,
                           COALESCE(SUM(amount), 0) as total_amount,
                           ROUND(COALESCE(AVG(amount), 0)::numeric, 2) as avg_amount,
                           COALESCE(MIN(amount), 0) as min_amount,
                           COALESCE(MAX(amount), 0) as max_amount,
                           COUNT(DISTINCT CASE WHEN buyer_nick IS NOT NULL AND buyer_nick != '''' THEN buyer_nick END) as distinct_buyer,
                           COALESCE(SUM(cost), 0) as total_cost,
                           COALESCE(SUM(gross_profit), 0) as total_profit
                    FROM (%s) sub
                    WHERE %I IS NOT NULL
                    GROUP BY %I
                    ORDER BY COALESCE(SUM(amount), 0) DESC
                    LIMIT %s
                 ) t',
                group_col, base_q, group_col, group_col, p_limit
            ) INTO result;
        END IF;
    END IF;

    RETURN COALESCE(result, '{}'::jsonb);
END;
$$;

COMMENT ON FUNCTION erp_global_stats_query IS
    'ERP全局统计RPC — 106: 新增 AVG/MIN/MAX + distinct_buyer + cost/profit';

-- 091: 为两个 RPC 函数的 filter DSL 补充 between 操作符支持
-- 根因: validate_filters 和 DuckDB export 均支持 between，但 RPC CASE 缺失导致 summary 模式静默丢弃
-- 影响: "金额在100-500之间的订单" 在 summary 模式下将正确过滤

-- ── erp_global_stats_query: 在 is_null 分支后追加 between ──
CREATE OR REPLACE FUNCTION erp_global_stats_query(
    p_doc_type VARCHAR,
    p_start VARCHAR DEFAULT NULL,
    p_end VARCHAR DEFAULT NULL,
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
LANGUAGE plpgsql SECURITY DEFINER
AS $$
DECLARE
    base_q TEXT;
    f JSONB;
    field_name TEXT;
    op TEXT;
    val JSONB;
    val_text TEXT;
    group_col TEXT;
    name_col TEXT;
    result JSONB;
    i INT;
BEGIN
    -- 基础查询
    base_q := 'SELECT * FROM erp_document_items WHERE doc_type = ' || quote_literal(p_doc_type);

    -- org_id 隔离
    IF p_org_id IS NOT NULL THEN
        base_q := base_q || ' AND org_id = ' || quote_literal(p_org_id);
    END IF;

    -- 时间范围
    IF p_start IS NOT NULL AND p_end IS NOT NULL THEN
        base_q := base_q || format(' AND %I >= %L AND %I < %L',
            p_time_col, p_start, p_time_col, p_end);
    END IF;

    -- 命名参数
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

    -- DSL 过滤器
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
            ) FROM (%s) sub',
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

        name_col := CASE p_group_by
            WHEN 'product' THEN ', MAX(item_name) as item_name'
            ELSE ''
        END;

        EXECUTE format(
            'SELECT COALESCE(jsonb_agg(row_to_jsonb(sub)), ''[]''::jsonb)
             FROM (
                SELECT %s as group_key,
                       COUNT(DISTINCT doc_id) as doc_count,
                       COALESCE(SUM(quantity), 0) as total_qty,
                       COALESCE(SUM(amount), 0) as total_amount
                       %s
                FROM (%s) t
                GROUP BY %s
                ORDER BY total_amount DESC
                LIMIT %s
             ) sub',
            group_col, name_col, base_q, group_col, p_limit
        ) INTO result;
    END IF;

    RETURN COALESCE(result, '{}'::jsonb);
END;
$$;


-- ── erp_order_stats_grouped: 同样补充 between ──
CREATE OR REPLACE FUNCTION erp_order_stats_grouped(
    p_org_id UUID DEFAULT NULL,
    p_start VARCHAR DEFAULT NULL,
    p_end VARCHAR DEFAULT NULL,
    p_time_col VARCHAR DEFAULT 'pay_time',
    p_filters JSONB DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
AS $$
DECLARE
    base_q TEXT;
    f JSONB;
    field_name TEXT;
    op TEXT;
    val JSONB;
    val_text TEXT;
    result JSONB;
    i INT;
BEGIN
    base_q := 'SELECT * FROM erp_document_items WHERE doc_type = ''order''';

    IF p_org_id IS NOT NULL THEN
        base_q := base_q || ' AND org_id = ' || quote_literal(p_org_id);
    END IF;

    IF p_start IS NOT NULL AND p_end IS NOT NULL THEN
        base_q := base_q || format(' AND %I >= %L AND %I < %L',
            p_time_col, p_start, p_time_col, p_end);
    END IF;

    -- DSL 过滤器
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

    -- 按 order_type + order_status 分组聚合
    EXECUTE format(
        'SELECT COALESCE(jsonb_agg(row_to_jsonb(sub)), ''[]''::jsonb)
         FROM (
            SELECT order_type,
                   order_status,
                   is_scalping,
                   COUNT(DISTINCT doc_id) as doc_count,
                   COALESCE(SUM(quantity), 0) as total_qty,
                   COALESCE(SUM(amount), 0) as total_amount
            FROM (%s) t
            GROUP BY order_type, order_status, is_scalping
            ORDER BY total_amount DESC
         ) sub',
        base_q
    ) INTO result;

    RETURN COALESCE(result, '[]'::jsonb);
END;
$$;

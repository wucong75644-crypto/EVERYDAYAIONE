-- ============================================================
-- 105: 分布分析 RPC — erp_distribution_query
-- 按数值字段动态分桶，返回各区间的计数和总量。
--
-- 用途：
--   "订单金额分布" / "客单价区间" / "数量分布"
--
-- 参数：
--   p_org_id    — 租户 ID
--   p_table     — 表名（erp_document_items / erp_product_daily_stats）
--   p_doc_type  — 单据类型（order/purchase/aftersale 等，可 NULL）
--   p_start     — 时间起点
--   p_end       — 时间终点
--   p_field     — 分桶字段（amount/quantity/order_amount 等）
--   p_buckets   — 分桶边界数组（如 {0,50,100,200,500,1000,5000}）
--   p_time_col  — 时间列名（默认 doc_created_at）
--
-- 返回 JSONB 数组：
--   [{"bucket": "0~50", "count": 120, "bucket_total": 3800.00}, ...]
-- ============================================================

CREATE OR REPLACE FUNCTION erp_distribution_query(
    p_org_id    UUID,
    p_table     TEXT      DEFAULT 'erp_document_items',
    p_doc_type  TEXT      DEFAULT NULL,
    p_start     TEXT      DEFAULT NULL,
    p_end       TEXT      DEFAULT NULL,
    p_field     TEXT      DEFAULT 'amount',
    p_buckets   NUMERIC[] DEFAULT '{0,50,100,200,500,1000,5000}',
    p_time_col  TEXT      DEFAULT 'doc_created_at'
) RETURNS JSONB AS $$
DECLARE
    v_case_lines  TEXT[] := '{}';
    v_label       TEXT;
    v_lo          NUMERIC;
    v_hi          NUMERIC;
    v_safe_field  TEXT;
    v_safe_table  TEXT;
    v_safe_tcol   TEXT;
    v_where       TEXT := '';
    v_sql         TEXT;
    v_result      JSONB;
BEGIN
    -- ── 安全校验：表名白名单 ──
    IF p_table NOT IN (
        'erp_document_items', 'erp_document_items_archive',
        'erp_product_daily_stats', 'erp_stock_status'
    ) THEN
        RAISE EXCEPTION 'table not allowed: %', p_table;
    END IF;

    -- ── 安全校验：字段名白名单 ──
    v_safe_field := CASE
        WHEN p_field IN (
            'amount', 'quantity', 'cost', 'gross_profit',
            'order_amount', 'order_qty', 'order_count', 'order_cost',
            'purchase_amount', 'purchase_qty', 'purchase_count',
            'aftersale_amount', 'aftersale_qty', 'aftersale_count',
            'total_stock', 'available_stock', 'sellable_num',
            'purchase_price', 'selling_price'
        ) THEN p_field
        ELSE NULL
    END;
    IF v_safe_field IS NULL THEN
        RAISE EXCEPTION 'field not allowed: %', p_field;
    END IF;

    -- ── 安全校验：时间列白名单 ──
    v_safe_tcol := CASE
        WHEN p_time_col IN (
            'doc_created_at', 'pay_time', 'consign_time',
            'stat_date', 'stock_modified_time'
        ) THEN p_time_col
        ELSE 'doc_created_at'
    END;
    v_safe_table := p_table;

    -- ── 构建 CASE WHEN 分桶表达式 ──
    FOR i IN 1 .. array_length(p_buckets, 1) LOOP
        v_lo := p_buckets[i];
        IF i < array_length(p_buckets, 1) THEN
            v_hi := p_buckets[i + 1];
            v_label := v_lo::TEXT || '~' || v_hi::TEXT;
            v_case_lines := v_case_lines || format(
                'WHEN %I >= %s AND %I < %s THEN %L',
                v_safe_field, v_lo, v_safe_field, v_hi, v_label
            );
        ELSE
            -- 最后一个桶：>= 最后边界
            v_label := v_lo::TEXT || '+';
            v_case_lines := v_case_lines || format(
                'WHEN %I >= %s THEN %L',
                v_safe_field, v_lo, v_label
            );
        END IF;
    END LOOP;

    -- ── WHERE 条件 ──
    v_where := format('WHERE org_id = %L', p_org_id);

    IF p_doc_type IS NOT NULL THEN
        v_where := v_where || format(' AND doc_type = %L', p_doc_type);
    END IF;

    IF p_start IS NOT NULL THEN
        v_where := v_where || format(' AND %I >= %L', v_safe_tcol, p_start);
    END IF;
    IF p_end IS NOT NULL THEN
        v_where := v_where || format(' AND %I < %L', v_safe_tcol, p_end);
    END IF;

    -- 排除 NULL 和负值
    v_where := v_where || format(' AND %I IS NOT NULL AND %I >= 0', v_safe_field, v_safe_field);

    -- ── 拼装最终 SQL ──
    v_sql := format(
        'SELECT jsonb_agg(row_to_json(t) ORDER BY t.sort_key) FROM ('
        '  SELECT'
        '    CASE %s ELSE ''other'' END AS bucket,'
        '    COUNT(*) AS count,'
        '    ROUND(SUM(%I)::NUMERIC, 2) AS bucket_total,'
        '    MIN(%I) AS sort_key'
        '  FROM %I %s'
        '  GROUP BY bucket'
        ') t',
        array_to_string(v_case_lines, ' '),
        v_safe_field,
        v_safe_field,
        v_safe_table,
        v_where
    );

    EXECUTE v_sql INTO v_result;
    RETURN COALESCE(v_result, '[]'::JSONB);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ── 权限 ──
GRANT EXECUTE ON FUNCTION erp_distribution_query(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, NUMERIC[], TEXT)
    TO authenticated, service_role;

COMMENT ON FUNCTION erp_distribution_query IS
    '分布分析 RPC — 按数值字段动态分桶，返回各区间计数和总量';

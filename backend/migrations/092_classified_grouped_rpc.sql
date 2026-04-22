-- ============================================================
-- 092: erp_order_stats_grouped 扩展 — 支持用户维度分组
--
-- 新增 p_group_by 参数：
--   NULL → 和之前一样，只按 (order_type, order_status, is_scalping) 分组
--   'platform'/'shop'/'product'/... → 按 (用户维度, order_type, order_status, is_scalping) 分组
--     输出多一列 group_key（用户维度值），Python 端按 group_key 分桶后逐桶分类
--
-- 设计目标：所有订单统计场景统一走此 RPC + 分类引擎，不再新建 RPC
-- ============================================================

-- 先 DROP 旧签名（5 参数），再 CREATE 新签名（6 参数）
-- 不能直接 CREATE OR REPLACE，因为 PG 视不同参数数量为不同函数
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
    -- Filter DSL 变量
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

    -- ── Filter DSL 解析 ─────────────────────────────────────
    -- FILTER_WHITELIST_SYNC: 与 erp_global_stats_query 保持同步
    IF p_filters IS NOT NULL AND jsonb_typeof(p_filters) = 'array' THEN
        FOR i IN 0..jsonb_array_length(p_filters) - 1 LOOP
            f := p_filters->i;
            field_name := f->>'field';
            op := f->>'op';
            val := f->'value';
            val_text := val#>>'{}';

            IF field_name NOT IN (
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
                -- 081 新增字段
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

    -- ── 聚合：分类维度 + 可选用户维度 ──
    IF p_group_by IS NULL THEN
        -- 无用户分组：和之前一样，只按分类维度
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
        -- 有用户分组：按 (用户维度, 分类维度) 二维分组
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
            EXECUTE format(
                'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
                'FROM ('
                '  SELECT %I AS group_key, MAX(item_name) AS item_name, '
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
    '订单分类统计RPC — 按 (用户维度, order_type, order_status, is_scalping) 分组，供分类引擎消费';

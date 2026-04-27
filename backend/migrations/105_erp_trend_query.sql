-- 105: 新增 erp_trend_query RPC — 趋势分析（按天/周/月分桶聚合）
--
-- 从 erp_product_daily_stats 表读取预聚合数据，
-- 支持 DATE_TRUNC 时间分桶 + 多指标 SUM + 可选分组（platform/shop_name/outer_id）。
--
-- 参数:
--   p_org_id      — 组织 ID（多租户隔离）
--   p_start       — 开始日期（含）
--   p_end         — 结束日期（不含）
--   p_granularity — 时间粒度: day / week / month
--   p_metrics     — 指标数组: {order_count,order_amount,...}
--   p_group_by    — 分组维度: NULL / outer_id / platform / shop_name
--   p_outer_id    — 商品编码过滤（可选）
--   p_platform    — 平台过滤（可选）
--   p_shop_name   — 店铺过滤（可选）
--   p_limit       — 返回行数上限
--
-- 依赖: Phase 0 已完成（daily_stats 已有 platform + shop_name 列）
-- 影响: 纯新增函数，不改现有 RPC

CREATE OR REPLACE FUNCTION erp_trend_query(
    p_org_id      UUID,
    p_start       DATE,
    p_end         DATE,
    p_granularity TEXT    DEFAULT 'day',
    p_metrics     TEXT[]  DEFAULT '{order_count,order_amount}',
    p_group_by    TEXT    DEFAULT NULL,
    p_outer_id    TEXT    DEFAULT NULL,
    p_platform    TEXT    DEFAULT NULL,
    p_shop_name   TEXT    DEFAULT NULL,
    p_limit       INT     DEFAULT 366
) RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    v_date_expr   TEXT;
    v_select_cols TEXT;
    v_where_extra TEXT := '';
    v_group_extra TEXT := '';
    v_group_sel   TEXT := '';
    v_result      JSONB;
    v_metric      TEXT;
    v_parts       TEXT[] := '{}';
    -- 白名单：允许的指标列（防止 SQL 注入）
    v_allowed     TEXT[] := ARRAY[
        'order_count', 'order_qty', 'order_amount', 'order_cost',
        'order_shipped_count', 'order_finished_count',
        'order_refund_count', 'order_cancelled_count',
        'aftersale_count', 'aftersale_refund_count',
        'aftersale_return_count', 'aftersale_exchange_count',
        'aftersale_reissue_count', 'aftersale_reject_count',
        'aftersale_repair_count', 'aftersale_other_count',
        'aftersale_qty', 'aftersale_amount',
        'purchase_count', 'purchase_qty',
        'purchase_received_qty', 'purchase_amount',
        'receipt_count', 'receipt_qty',
        'shelf_count', 'shelf_qty',
        'purchase_return_count', 'purchase_return_qty', 'purchase_return_amount'
    ];
BEGIN
    -- 参数校验
    IF p_start > p_end THEN
        RETURN jsonb_build_object('error', 'p_start must <= p_end');
    END IF;

    -- 时间分桶表达式
    v_date_expr := CASE p_granularity
        WHEN 'day'   THEN 'stat_date'
        WHEN 'week'  THEN 'date_trunc(''week'', stat_date)::date'
        WHEN 'month' THEN 'date_trunc(''month'', stat_date)::date'
        ELSE 'stat_date'  -- 未知粒度降级为 day
    END;

    -- 动态构建 SELECT 列（只返回用户要的 metrics，白名单校验）
    FOREACH v_metric IN ARRAY p_metrics LOOP
        IF v_metric = ANY(v_allowed) THEN
            v_parts := array_append(v_parts,
                format('COALESCE(SUM(%I), 0) AS %I', v_metric, v_metric));
        END IF;
    END LOOP;

    -- 至少保留一个指标
    IF array_length(v_parts, 1) IS NULL OR array_length(v_parts, 1) = 0 THEN
        v_parts := ARRAY['COALESCE(SUM(order_count), 0) AS order_count',
                         'COALESCE(SUM(order_amount), 0) AS order_amount'];
    END IF;

    v_select_cols := array_to_string(v_parts, ', ');

    -- 可选 WHERE 条件
    IF p_outer_id IS NOT NULL THEN
        v_where_extra := v_where_extra || format(' AND outer_id = %L', p_outer_id);
    END IF;
    IF p_platform IS NOT NULL THEN
        v_where_extra := v_where_extra || format(' AND platform = %L', p_platform);
    END IF;
    IF p_shop_name IS NOT NULL THEN
        v_where_extra := v_where_extra || format(' AND shop_name ILIKE %L', '%%' || p_shop_name || '%%');
    END IF;

    -- 可选 GROUP BY 维度（除了时间分桶外的额外分组）
    IF p_group_by IS NOT NULL THEN
        IF p_group_by IN ('outer_id', 'platform', 'shop_name') THEN
            v_group_extra := format(', %I', p_group_by);
            v_group_sel := format(', %I AS group_key', p_group_by);
        ELSIF p_group_by = 'sku_outer_id' THEN
            v_group_extra := ', sku_outer_id';
            v_group_sel := ', sku_outer_id AS group_key';
        END IF;
    END IF;

    -- 执行查询
    EXECUTE format(
        'SELECT COALESCE(jsonb_agg(row_to_json(t)), ''[]''::jsonb) '
        'FROM ('
        '  SELECT %s AS period %s, %s'
        '  FROM erp_product_daily_stats'
        '  WHERE org_id = $1'
        '    AND stat_date >= $2 AND stat_date < $3'
        '    %s'
        '  GROUP BY %s %s'
        '  ORDER BY %s %s'
        '  LIMIT $4'
        ') t',
        v_date_expr,         -- period
        v_group_sel,         -- , group_key（如果有分组）
        v_select_cols,       -- SUM(order_count) AS order_count, ...
        v_where_extra,       -- AND outer_id = ... AND platform = ...
        v_date_expr,         -- GROUP BY period
        v_group_extra,       -- , outer_id（如果有分组）
        v_date_expr,         -- ORDER BY period
        v_group_extra        -- , outer_id（如果有分组）
    ) INTO v_result USING p_org_id, p_start, p_end, p_limit;

    RETURN COALESCE(v_result, '[]'::jsonb);
END;
$$;

COMMENT ON FUNCTION erp_trend_query IS
    '趋势分析RPC — 105: daily_stats 时间分桶 + 多指标聚合 + platform/shop/product 分组';

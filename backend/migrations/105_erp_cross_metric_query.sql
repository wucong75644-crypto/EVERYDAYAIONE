-- ═══════════════════════════════════════════════════════════════════════
-- 105_erp_cross_metric_query.sql
-- 跨域指标分析 RPC（Phase 6 — ERP 查询架构重构）
--
-- 三个函数：
--   1. erp_cross_metric_query    — daily_stats 比率/均值指标（10 个指标）
--   2. erp_repurchase_rate_query — 复购率（buyer_nick 子查询）
--   3. erp_ship_time_query       — 发货时效（consign_time − pay_time）
--
-- 设计文档: docs/document/TECH_ERP查询架构重构.md §5.6
-- ═══════════════════════════════════════════════════════════════════════


-- ═══════════════════════════════════════════════════════════════════════
-- 1. erp_cross_metric_query
--    基于 erp_product_daily_stats 的比率 / 均值指标。
--    支持按 platform / shop_name / outer_id 分组，
--    支持按 day / week / month 时间粒度聚合。
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION erp_cross_metric_query(
    p_org_id      UUID,
    p_start       DATE,
    p_end         DATE,
    p_metric      TEXT,                   -- 指标名称（见下方白名单）
    p_group_by    TEXT    DEFAULT NULL,    -- outer_id | platform | shop_name
    p_granularity TEXT    DEFAULT NULL,    -- day | week | month（NULL = 总计）
    p_outer_id    TEXT    DEFAULT NULL,    -- 按商品编码过滤
    p_platform    TEXT    DEFAULT NULL,    -- 按平台过滤
    p_shop_name   TEXT    DEFAULT NULL,    -- 按店铺过滤
    p_limit       INT     DEFAULT 50
) RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_num       TEXT;           -- 分子表达式
    v_den       TEXT;           -- 分母表达式（NULL = 绝对值指标）
    v_mult      INT;            -- 乘数：100 = 百分比，1 = 原值
    v_date_expr TEXT;
    v_group_col TEXT;
    v_sel       TEXT[] := '{}'; -- SELECT 子句片段
    v_grp       TEXT[] := '{}'; -- GROUP BY 子句片段
    v_where     TEXT   := '';   -- 额外 WHERE 条件
    v_order     TEXT;
    v_sql       TEXT;
    v_result    JSONB;
BEGIN
    -- ── 指标公式映射 ─────────────────────────────────
    CASE p_metric
        WHEN 'return_rate' THEN            -- 退货率
            v_num := 'SUM(aftersale_return_count)';
            v_den := 'SUM(order_count)';            v_mult := 100;
        WHEN 'refund_rate' THEN            -- 退款率
            v_num := 'SUM(aftersale_refund_count)';
            v_den := 'SUM(order_count)';            v_mult := 100;
        WHEN 'exchange_rate' THEN          -- 换货率
            v_num := 'SUM(aftersale_exchange_count)';
            v_den := 'SUM(order_count)';            v_mult := 100;
        WHEN 'aftersale_rate' THEN         -- 售后率
            v_num := 'SUM(aftersale_count)';
            v_den := 'SUM(order_count)';            v_mult := 100;
        WHEN 'avg_order_value' THEN        -- 客单价（元）
            v_num := 'SUM(order_amount)';
            v_den := 'SUM(order_count)';            v_mult := 1;
        WHEN 'gross_margin' THEN           -- 毛利率
            v_num := 'SUM(order_amount) - SUM(order_cost)';
            v_den := 'SUM(order_amount)';            v_mult := 100;
        WHEN 'gross_profit' THEN           -- 毛利额（元）
            v_num := 'SUM(order_amount) - SUM(order_cost)';
            v_den := NULL;                  v_mult := 1;
        WHEN 'purchase_fulfillment' THEN   -- 采购达成率
            v_num := 'SUM(receipt_count)';
            v_den := 'SUM(purchase_count)';           v_mult := 100;
        WHEN 'shelf_rate' THEN             -- 上架率
            v_num := 'SUM(shelf_count)';
            v_den := 'SUM(receipt_count)';            v_mult := 100;
        WHEN 'supplier_return_rate' THEN   -- 供应商退货率
            v_num := 'SUM(purchase_return_count)';
            v_den := 'SUM(purchase_count)';           v_mult := 100;
        ELSE
            RETURN jsonb_build_object('error', 'unknown metric: ' || p_metric);
    END CASE;

    -- ── 时间粒度 ─────────────────────────────────────
    IF p_granularity IS NOT NULL THEN
        v_date_expr := CASE p_granularity
            WHEN 'day'   THEN 'stat_date'
            WHEN 'week'  THEN 'date_trunc(''week'', stat_date)::date'
            WHEN 'month' THEN 'date_trunc(''month'', stat_date)::date'
            ELSE 'stat_date'
        END;
    END IF;

    -- ── 分组列 ───────────────────────────────────────
    IF p_group_by IS NOT NULL THEN
        v_group_col := CASE p_group_by
            WHEN 'outer_id'  THEN 'outer_id'
            WHEN 'platform'  THEN 'platform'
            WHEN 'shop_name' THEN 'shop_name'
            ELSE NULL
        END;
        IF v_group_col IS NULL THEN
            RETURN jsonb_build_object('error', 'invalid group_by: ' || p_group_by);
        END IF;
    END IF;

    -- ── 构建 SELECT ──────────────────────────────────

    -- period 列
    IF v_date_expr IS NOT NULL THEN
        v_sel := array_append(v_sel, v_date_expr || ' AS period');
        v_grp := array_append(v_grp, v_date_expr);
    ELSE
        v_sel := array_append(v_sel, 'NULL::date AS period');
    END IF;

    -- group_key 列
    IF v_group_col IS NOT NULL THEN
        v_sel := array_append(v_sel, v_group_col || ' AS group_key');
        v_grp := array_append(v_grp, v_group_col);
        IF v_group_col = 'outer_id' THEN
            v_sel := array_append(v_sel, 'MIN(item_name) AS item_name');
        END IF;
    ELSE
        v_sel := array_append(v_sel, 'NULL::text AS group_key');
    END IF;

    -- metric_value + numerator + denominator
    IF v_den IS NOT NULL THEN
        v_sel := array_append(v_sel, format(
            'ROUND((%s)::numeric / NULLIF((%s)::numeric, 0) * %s, 2) AS metric_value',
            v_num, v_den, v_mult
        ));
        v_sel := array_append(v_sel, format('(%s)::numeric AS numerator', v_num));
        v_sel := array_append(v_sel, format('(%s)::numeric AS denominator', v_den));
    ELSE
        v_sel := array_append(v_sel, format(
            'ROUND((%s)::numeric, 2) AS metric_value', v_num
        ));
        v_sel := array_append(v_sel, format('(%s)::numeric AS numerator', v_num));
        v_sel := array_append(v_sel, 'NULL::numeric AS denominator');
    END IF;

    -- ── 额外 WHERE ──────────────────────────────────
    IF p_outer_id IS NOT NULL THEN
        v_where := v_where || format(' AND outer_id = %L', p_outer_id);
    END IF;
    IF p_platform IS NOT NULL THEN
        v_where := v_where || format(' AND platform = %L', p_platform);
    END IF;
    IF p_shop_name IS NOT NULL THEN
        v_where := v_where || format(' AND shop_name ILIKE %L', '%' || p_shop_name || '%');
    END IF;

    -- ── ORDER BY ─────────────────────────────────────
    IF v_date_expr IS NOT NULL AND v_group_col IS NOT NULL THEN
        v_order := v_date_expr || ', metric_value DESC NULLS LAST';
    ELSIF v_date_expr IS NOT NULL THEN
        v_order := v_date_expr;
    ELSE
        v_order := 'metric_value DESC NULLS LAST';
    END IF;

    -- ── 组装完整 SQL ─────────────────────────────────
    v_sql := format(
        'SELECT jsonb_agg(row_to_json(t)) FROM ('
        '  SELECT %s'
        '  FROM erp_product_daily_stats'
        '  WHERE org_id = %L'
        '    AND stat_date >= %L AND stat_date < %L'
        '    %s'
        '  %s'
        '  ORDER BY %s'
        '  LIMIT %s'
        ') t',
        array_to_string(v_sel, ', '),
        p_org_id,
        p_start, p_end,
        v_where,
        CASE WHEN array_length(v_grp, 1) > 0
             THEN 'GROUP BY ' || array_to_string(v_grp, ', ')
             ELSE '' END,
        v_order,
        p_limit
    );

    EXECUTE v_sql INTO v_result;
    RETURN COALESCE(v_result, '[]'::jsonb);
END;
$$;


-- ═══════════════════════════════════════════════════════════════════════
-- 2. erp_repurchase_rate_query
--    复购率 = 购买≥2次的买家数 / 去重买家总数 × 100
--    数据源：erp_document_items（+ archive）
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION erp_repurchase_rate_query(
    p_org_id    UUID,
    p_start     TIMESTAMPTZ,
    p_end       TIMESTAMPTZ,
    p_group_by  TEXT    DEFAULT NULL,    -- platform | shop_name
    p_platform  TEXT    DEFAULT NULL,
    p_shop_name TEXT    DEFAULT NULL,
    p_limit     INT     DEFAULT 50
) RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_group_col    TEXT;
    v_grp_select   TEXT := '';
    v_grp_inner    TEXT := '';
    v_grp_outer    TEXT := '';
    v_where        TEXT := '';
    v_from         TEXT;
    v_sql          TEXT;
    v_result       JSONB;
    v_need_archive BOOLEAN;
BEGIN
    -- 分组列
    IF p_group_by IS NOT NULL THEN
        v_group_col := CASE p_group_by
            WHEN 'platform'  THEN 'platform'
            WHEN 'shop_name' THEN 'shop_name'
            ELSE NULL
        END;
        IF v_group_col IS NOT NULL THEN
            v_grp_select := v_group_col || ' AS group_key, ';
            v_grp_inner  := ', ' || v_group_col;
            v_grp_outer  := 'GROUP BY ' || v_group_col;
        END IF;
    END IF;

    -- 额外过滤
    IF p_platform IS NOT NULL THEN
        v_where := v_where || format(' AND platform = %L', p_platform);
    END IF;
    IF p_shop_name IS NOT NULL THEN
        v_where := v_where || format(' AND shop_name ILIKE %L', '%' || p_shop_name || '%');
    END IF;

    -- 是否需要归档表（起始时间 > 90 天前）
    v_need_archive := p_start < (CURRENT_TIMESTAMP - INTERVAL '90 days');

    -- FROM 子句（主表 + 可选归档表）
    IF v_need_archive THEN
        v_from := format(
            '(SELECT buyer_nick, doc_id %s FROM erp_document_items'
            '   WHERE doc_type = ''order'' AND org_id = %L'
            '     AND doc_created_at >= %L AND doc_created_at < %L'
            '     AND buyer_nick IS NOT NULL AND buyer_nick != '''''
            '     %s'
            ' UNION ALL'
            ' SELECT buyer_nick, doc_id %s FROM erp_document_items_archive'
            '   WHERE doc_type = ''order'' AND org_id = %L'
            '     AND doc_created_at >= %L AND doc_created_at < %L'
            '     AND buyer_nick IS NOT NULL AND buyer_nick != '''''
            '     %s'
            ') combined',
            v_grp_inner, p_org_id, p_start, p_end, v_where,
            v_grp_inner, p_org_id, p_start, p_end, v_where
        );
    ELSE
        v_from := format(
            'erp_document_items'
            ' WHERE doc_type = ''order'' AND org_id = %L'
            '   AND doc_created_at >= %L AND doc_created_at < %L'
            '   AND buyer_nick IS NOT NULL AND buyer_nick != '''''
            '   %s',
            p_org_id, p_start, p_end, v_where
        );
        -- 非归档：需要把 WHERE 从 FROM 里提出来给子查询
        -- 重新组织：用子查询统一写法
        v_from := format(
            '(SELECT buyer_nick, doc_id %s FROM erp_document_items'
            '   WHERE doc_type = ''order'' AND org_id = %L'
            '     AND doc_created_at >= %L AND doc_created_at < %L'
            '     AND buyer_nick IS NOT NULL AND buyer_nick != '''''
            '     %s'
            ') combined',
            v_grp_inner, p_org_id, p_start, p_end, v_where
        );
    END IF;

    v_sql := format(
        'SELECT jsonb_agg(row_to_json(t)) FROM ('
        '  SELECT'
        '    %s'
        '    ROUND('
        '      COUNT(DISTINCT CASE WHEN cnt >= 2 THEN buyer_nick END)::numeric /'
        '      NULLIF(COUNT(DISTINCT buyer_nick), 0) * 100, 2'
        '    ) AS metric_value,'
        '    COUNT(DISTINCT buyer_nick) AS total_buyers,'
        '    COUNT(DISTINCT CASE WHEN cnt >= 2 THEN buyer_nick END) AS repeat_buyers'
        '  FROM ('
        '    SELECT buyer_nick %s, COUNT(DISTINCT doc_id) AS cnt'
        '    FROM %s'
        '    GROUP BY buyer_nick %s'
        '  ) sub'
        '  %s'
        '  ORDER BY metric_value DESC NULLS LAST'
        '  LIMIT %s'
        ') t',
        v_grp_select,
        v_grp_inner,
        v_from,
        v_grp_inner,
        v_grp_outer,
        p_limit
    );

    EXECUTE v_sql INTO v_result;
    RETURN COALESCE(v_result, '[]'::jsonb);
END;
$$;


-- ═══════════════════════════════════════════════════════════════════════
-- 3. erp_ship_time_query
--    发货时效 = AVG(consign_time − pay_time)
--    当日发货率 = COUNT(差值 < 24h) / COUNT(*)
--    数据源：erp_document_items（+ archive）
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION erp_ship_time_query(
    p_org_id    UUID,
    p_start     TIMESTAMPTZ,
    p_end       TIMESTAMPTZ,
    p_group_by  TEXT    DEFAULT NULL,    -- platform | shop_name
    p_platform  TEXT    DEFAULT NULL,
    p_shop_name TEXT    DEFAULT NULL,
    p_limit     INT     DEFAULT 50
) RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_group_col  TEXT;
    v_grp_select TEXT := '';
    v_grp_clause TEXT := '';
    v_where      TEXT := '';
    v_from       TEXT;
    v_sql        TEXT;
    v_result     JSONB;
    v_need_archive BOOLEAN;
BEGIN
    -- 分组列
    IF p_group_by IS NOT NULL THEN
        v_group_col := CASE p_group_by
            WHEN 'platform'  THEN 'platform'
            WHEN 'shop_name' THEN 'shop_name'
            ELSE NULL
        END;
        IF v_group_col IS NOT NULL THEN
            v_grp_select := v_group_col || ' AS group_key, ';
            v_grp_clause := 'GROUP BY ' || v_group_col;
        END IF;
    END IF;

    -- 额外过滤
    IF p_platform IS NOT NULL THEN
        v_where := v_where || format(' AND platform = %L', p_platform);
    END IF;
    IF p_shop_name IS NOT NULL THEN
        v_where := v_where || format(' AND shop_name ILIKE %L', '%' || p_shop_name || '%');
    END IF;

    -- 归档表判断
    v_need_archive := p_start < (CURRENT_TIMESTAMP - INTERVAL '90 days');

    -- 基础 WHERE 条件（主表或 UNION ALL）
    IF v_need_archive THEN
        v_from := format(
            '(SELECT pay_time, consign_time %s FROM erp_document_items'
            '   WHERE doc_type = ''order'' AND org_id = %L'
            '     AND doc_created_at >= %L AND doc_created_at < %L'
            '     AND pay_time IS NOT NULL AND consign_time IS NOT NULL'
            '     AND consign_time > pay_time'
            '     %s'
            ' UNION ALL'
            ' SELECT pay_time, consign_time %s FROM erp_document_items_archive'
            '   WHERE doc_type = ''order'' AND org_id = %L'
            '     AND doc_created_at >= %L AND doc_created_at < %L'
            '     AND pay_time IS NOT NULL AND consign_time IS NOT NULL'
            '     AND consign_time > pay_time'
            '     %s'
            ') combined',
            CASE WHEN v_group_col IS NOT NULL THEN ', ' || v_group_col ELSE '' END,
            p_org_id, p_start, p_end, v_where,
            CASE WHEN v_group_col IS NOT NULL THEN ', ' || v_group_col ELSE '' END,
            p_org_id, p_start, p_end, v_where
        );
    ELSE
        v_from := format(
            '(SELECT pay_time, consign_time %s FROM erp_document_items'
            '   WHERE doc_type = ''order'' AND org_id = %L'
            '     AND doc_created_at >= %L AND doc_created_at < %L'
            '     AND pay_time IS NOT NULL AND consign_time IS NOT NULL'
            '     AND consign_time > pay_time'
            '     %s'
            ') combined',
            CASE WHEN v_group_col IS NOT NULL THEN ', ' || v_group_col ELSE '' END,
            p_org_id, p_start, p_end, v_where
        );
    END IF;

    v_sql := format(
        'SELECT jsonb_agg(row_to_json(t)) FROM ('
        '  SELECT'
        '    %s'
        '    ROUND(AVG(EXTRACT(EPOCH FROM (consign_time - pay_time)) / 3600)::numeric, 1)'
        '      AS avg_ship_hours,'
        '    ROUND('
        '      COUNT(CASE WHEN consign_time - pay_time < INTERVAL ''24 hours'' THEN 1 END)::numeric /'
        '      NULLIF(COUNT(*), 0) * 100, 2'
        '    ) AS same_day_rate,'
        '    COUNT(*) AS total_shipped'
        '  FROM %s'
        '  %s'
        '  ORDER BY avg_ship_hours ASC NULLS LAST'
        '  LIMIT %s'
        ') t',
        v_grp_select,
        v_from,
        v_grp_clause,
        p_limit
    );

    EXECUTE v_sql INTO v_result;
    RETURN COALESCE(v_result, '[]'::jsonb);
END;
$$;

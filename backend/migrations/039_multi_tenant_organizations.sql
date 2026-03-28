-- 039: 企业级多租户账号系统
-- 技术文档: docs/document/TECH_企业级多租户账号系统.md
-- 内容:
--   A. 4张新表（organizations, org_members, org_configs, org_invitations）
--   B. 19张现有表加 org_id
--   C. RPC 函数改造（加 org_id 参数）
--   D. 物化视图 mv_kit_stock 重建（加 org_id）
--   E. 索引

-- ════════════════════════════════════════════════════════
-- A. 新表
-- ════════════════════════════════════════════════════════

-- A1. 企业表
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    logo_url VARCHAR(500),
    owner_id UUID NOT NULL REFERENCES users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended')),
    max_members INTEGER NOT NULL DEFAULT 50,
    features JSONB NOT NULL DEFAULT '{"erp": false, "image_gen": true, "agent": true}',
    wecom_corp_id VARCHAR(100),
    wecom_agent_id VARCHAR(100),
    wecom_secret_encrypted TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_org_owner ON organizations(owner_id);
CREATE INDEX IF NOT EXISTS idx_org_status ON organizations(status);

COMMENT ON TABLE organizations IS '企业表（多租户），name 精确匹配登录';
COMMENT ON COLUMN organizations.features IS '功能开关：erp/image_gen/agent';
COMMENT ON COLUMN organizations.wecom_secret_encrypted IS 'AES-256-GCM 加密';

-- A2. 企业成员表（一人可属多企业）
CREATE TABLE IF NOT EXISTS org_members (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'member'
        CHECK (role IN ('owner', 'admin', 'member')),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled')),
    permissions JSONB NOT NULL DEFAULT '{}',
    invited_by UUID REFERENCES users(id),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_org_members_user ON org_members(user_id);

COMMENT ON TABLE org_members IS '企业成员（一人可属多企业）';
COMMENT ON COLUMN org_members.role IS 'owner=创建者, admin=管理员, member=成员';
COMMENT ON COLUMN org_members.permissions IS '预留细粒度权限 JSON';

-- A3. 企业配置表（AES-256-GCM 加密存储 API Key）
CREATE TABLE IF NOT EXISTS org_configs (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    config_key VARCHAR(100) NOT NULL,
    config_value_encrypted TEXT NOT NULL,
    updated_by UUID REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, config_key)
);

COMMENT ON TABLE org_configs IS '企业配置（API Key 等），值为 AES-256-GCM 加密';
COMMENT ON COLUMN org_configs.config_key IS '如 kuaimai_app_key, google_api_key 等';

-- A4. 企业邀请表
CREATE TABLE IF NOT EXISTS org_invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone VARCHAR(20) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member'
        CHECK (role IN ('admin', 'member')),
    invite_token VARCHAR(100) UNIQUE NOT NULL,
    invited_by UUID NOT NULL REFERENCES users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_org_invitations_phone ON org_invitations(phone);
CREATE INDEX IF NOT EXISTS idx_org_invitations_token ON org_invitations(invite_token);
CREATE INDEX IF NOT EXISTS idx_org_invitations_org ON org_invitations(org_id, status);

COMMENT ON TABLE org_invitations IS '企业邀请（手机号邀请加入企业）';

-- ════════════════════════════════════════════════════════
-- B. 现有表加 org_id
-- ════════════════════════════════════════════════════════
-- org_id 含义: NULL = 散客数据, 有值 = 企业数据

-- B1. users 表：当前活跃企业上下文
ALTER TABLE users ADD COLUMN IF NOT EXISTS current_org_id UUID REFERENCES organizations(id);

-- B2. 对话/任务
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);

-- B3. 积分
ALTER TABLE credits_history ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE credit_transactions ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);

-- B4. 生图
ALTER TABLE image_generations ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);

-- B5. 企微相关
ALTER TABLE wecom_user_mappings ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE wecom_chat_targets ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE wecom_departments ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE wecom_employees ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);

-- B6. ERP（10张表）
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_product_daily_stats ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_products ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_product_skus ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_stock_status ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_suppliers ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_product_platform_map ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_sync_state ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_sync_dead_letter ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);

-- B7. 知识图谱
ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE knowledge_metrics ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
ALTER TABLE scoring_audit_log ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);

-- ════════════════════════════════════════════════════════
-- B-IDX. org_id 索引（部分索引，仅非 NULL 行）
-- ════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_conversations_org ON conversations(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_org ON tasks(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_credits_history_org ON credits_history(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_credit_tx_org ON credit_transactions(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_image_gen_org ON image_generations(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wecom_mappings_org ON wecom_user_mappings(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_erp_doc_items_org ON erp_document_items(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_erp_archive_org ON erp_document_items_archive(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_erp_products_org ON erp_products(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_erp_stock_org ON erp_stock_status(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_erp_sync_state_org ON erp_sync_state(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_org ON knowledge_nodes(org_id) WHERE org_id IS NOT NULL;

-- erp_sync_state 唯一约束改为 (org_id, sync_type)
DROP INDEX IF EXISTS erp_sync_state_sync_type_key;
ALTER TABLE erp_sync_state DROP CONSTRAINT IF EXISTS erp_sync_state_sync_type_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_sync_state_org_type
    ON erp_sync_state (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), sync_type);

-- ⚠️ ERP 表唯一约束（outer_id, doc_id 等）暂不修改！
-- 原因：现有 Python 代码用 .upsert(on_conflict="outer_id") 依赖原始唯一约束，
-- 改成含 org_id 的表达式索引后 PostgREST 无法匹配，会导致 ERP 同步立即崩溃。
-- 唯一约束改造推迟到 Phase 7/8，与 Python upsert 调用方代码同步修改。
-- 涉及: erp_products, erp_product_skus, erp_document_items(_archive),
--       erp_product_daily_stats, erp_stock_status, erp_suppliers, erp_product_platform_map

-- ════════════════════════════════════════════════════════
-- C. RPC 函数改造（加 org_id 参数）
-- ════════════════════════════════════════════════════════

-- C1. deduct_credits_atomic：加 p_org_id 参数，写入 credits_history 时带 org_id
CREATE OR REPLACE FUNCTION deduct_credits_atomic(
    p_user_id UUID,
    p_amount INTEGER,
    p_reason TEXT,
    p_change_type TEXT,
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    v_new_balance INTEGER;
BEGIN
    UPDATE users
    SET credits = credits - p_amount,
        updated_at = NOW()
    WHERE id = p_user_id
      AND credits >= p_amount
    RETURNING credits INTO v_new_balance;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'message', 'Insufficient credits');
    END IF;

    INSERT INTO credits_history (user_id, change_type, change_amount, balance_after, description, org_id)
    VALUES (p_user_id, p_change_type::credits_change_type, -p_amount, v_new_balance, p_reason, p_org_id);

    RETURN jsonb_build_object('success', true, 'new_balance', v_new_balance);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION deduct_credits_atomic IS '原子扣除积分（多租户），p_org_id 可选';

-- C2. erp_global_stats_query：加 p_org_id 参数，WHERE 强制过滤
CREATE OR REPLACE FUNCTION erp_global_stats_query(
    p_doc_type VARCHAR,
    p_start TIMESTAMPTZ,
    p_end TIMESTAMPTZ,
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
BEGIN
    IF p_start > p_end THEN
        RETURN jsonb_build_object('error', 'p_start must <= p_end');
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
         WHERE doc_type = %L AND doc_created_at >= %L AND doc_created_at < %L',
        p_doc_type, p_start, p_end
    ) || org_filter;

    IF need_archive THEN
        base_q := base_q || format(
            ' UNION ALL
             SELECT doc_id, quantity, amount, outer_id, item_name,
                    shop_name, platform, supplier_name, warehouse_name,
                    doc_status, order_status
             FROM erp_document_items_archive
             WHERE doc_type = %L AND doc_created_at >= %L AND doc_created_at < %L',
            p_doc_type, p_start, LEAST(p_end, NOW() - INTERVAL '90 days')
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

COMMENT ON FUNCTION erp_global_stats_query IS 'ERP全局统计RPC（多租户），p_org_id 必传';

-- C3. erp_aggregate_daily_stats：加 p_org_id 参数
CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats(
    p_outer_id VARCHAR,
    p_stat_date DATE,
    p_org_id UUID DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO erp_product_daily_stats (
        stat_date, outer_id, sku_outer_id, item_name, org_id,
        purchase_count, purchase_qty, purchase_received_qty, purchase_amount,
        receipt_count, receipt_qty,
        shelf_count, shelf_qty,
        purchase_return_count, purchase_return_qty, purchase_return_amount,
        aftersale_count, aftersale_refund_count, aftersale_return_count,
        aftersale_exchange_count, aftersale_reissue_count,
        aftersale_reject_count, aftersale_repair_count, aftersale_other_count,
        aftersale_qty, aftersale_amount,
        order_count, order_qty, order_amount,
        order_shipped_count, order_finished_count,
        order_refund_count, order_cancelled_count, order_cost,
        updated_at
    )
    SELECT
        p_stat_date,
        p_outer_id,
        NULL,
        MAX(item_name),
        p_org_id,
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(quantity_received) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'receipt'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'receipt'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'shelf'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'shelf'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase_return'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale'),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type IN (1, 5)),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 2),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 4),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 3),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 7),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 9),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type IN (0, 8)),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'aftersale'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'aftersale'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'order'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'order'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND consign_time IS NOT NULL),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND order_status = 'FINISHED'),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND refund_status IS NOT NULL),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND (extra_json->>'isCancel')::int = 1),
        COALESCE(SUM(cost * quantity) FILTER(WHERE doc_type = 'order'), 0),
        NOW()
    FROM erp_document_items
    WHERE outer_id = p_outer_id
      AND doc_created_at::date = p_stat_date
      AND (p_org_id IS NULL AND org_id IS NULL OR org_id = p_org_id)
    -- ⚠️ ON CONFLICT 暂用原始唯一索引，Phase 7/8 改为含 org_id 的版本
    ON CONFLICT (stat_date, outer_id, COALESCE(sku_outer_id, ''))
    DO UPDATE SET
        item_name = EXCLUDED.item_name,
        org_id = EXCLUDED.org_id,
        purchase_count = EXCLUDED.purchase_count,
        purchase_qty = EXCLUDED.purchase_qty,
        purchase_received_qty = EXCLUDED.purchase_received_qty,
        purchase_amount = EXCLUDED.purchase_amount,
        receipt_count = EXCLUDED.receipt_count,
        receipt_qty = EXCLUDED.receipt_qty,
        shelf_count = EXCLUDED.shelf_count,
        shelf_qty = EXCLUDED.shelf_qty,
        purchase_return_count = EXCLUDED.purchase_return_count,
        purchase_return_qty = EXCLUDED.purchase_return_qty,
        purchase_return_amount = EXCLUDED.purchase_return_amount,
        aftersale_count = EXCLUDED.aftersale_count,
        aftersale_refund_count = EXCLUDED.aftersale_refund_count,
        aftersale_return_count = EXCLUDED.aftersale_return_count,
        aftersale_exchange_count = EXCLUDED.aftersale_exchange_count,
        aftersale_reissue_count = EXCLUDED.aftersale_reissue_count,
        aftersale_reject_count = EXCLUDED.aftersale_reject_count,
        aftersale_repair_count = EXCLUDED.aftersale_repair_count,
        aftersale_other_count = EXCLUDED.aftersale_other_count,
        aftersale_qty = EXCLUDED.aftersale_qty,
        aftersale_amount = EXCLUDED.aftersale_amount,
        order_count = EXCLUDED.order_count,
        order_qty = EXCLUDED.order_qty,
        order_amount = EXCLUDED.order_amount,
        order_shipped_count = EXCLUDED.order_shipped_count,
        order_finished_count = EXCLUDED.order_finished_count,
        order_refund_count = EXCLUDED.order_refund_count,
        order_cancelled_count = EXCLUDED.order_cancelled_count,
        order_cost = EXCLUDED.order_cost,
        updated_at = NOW();
END;
$$;

COMMENT ON FUNCTION erp_aggregate_daily_stats IS 'ERP每日聚合（多租户），p_org_id 可选';

-- C4. erp_try_acquire_sync_lock：改为按 (org_id, sync_type) 加锁
CREATE OR REPLACE FUNCTION erp_try_acquire_sync_lock(
    p_lock_ttl_seconds INT DEFAULT 300,
    p_org_id UUID DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_acquired BOOLEAN;
BEGIN
    UPDATE erp_sync_state
    SET status = 'running', last_run_at = NOW()
    WHERE sync_type = 'purchase'
      AND (p_org_id IS NULL AND org_id IS NULL OR org_id = p_org_id)
      AND (
          status != 'running'
          OR last_run_at < NOW() - (p_lock_ttl_seconds || ' seconds')::INTERVAL
      );

    GET DIAGNOSTICS v_acquired = ROW_COUNT;
    RETURN v_acquired > 0;
END;
$$;

COMMENT ON FUNCTION erp_try_acquire_sync_lock IS 'ERP同步锁（多租户），按 org_id 隔离';

-- C5. erp_aggregate_daily_stats_batch：加 p_org_id 参数
CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats_batch(
    p_since_date DATE,
    p_org_id UUID DEFAULT NULL
) RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INTEGER := 0;
    v_rec RECORD;
BEGIN
    FOR v_rec IN
        SELECT DISTINCT
            outer_id,
            (doc_created_at::DATE)::TEXT AS stat_date
        FROM erp_document_items
        WHERE doc_created_at >= p_since_date
          AND outer_id IS NOT NULL
          AND (p_org_id IS NULL AND org_id IS NULL OR org_id = p_org_id)
    LOOP
        PERFORM erp_aggregate_daily_stats(v_rec.outer_id, v_rec.stat_date, p_org_id);
        v_count := v_count + 1;
    END LOOP;

    RETURN v_count;
END;
$$;

COMMENT ON FUNCTION erp_aggregate_daily_stats_batch IS 'ERP批量每日聚合（多租户），p_org_id 可选';

-- ════════════════════════════════════════════════════════
-- D. 物化视图 mv_kit_stock 重建（加 org_id）
-- ════════════════════════════════════════════════════════

DROP MATERIALIZED VIEW IF EXISTS mv_kit_stock;

CREATE MATERIALIZED VIEW mv_kit_stock AS
WITH kit_components AS (
    SELECT
        p.org_id                                AS org_id,
        p.outer_id                              AS kit_outer_id,
        comp->>'skuOuterId'                     AS kit_sku_outer_id,
        comp->>'outerId'                        AS sub_code,
        GREATEST((comp->>'ratio')::int, 1)      AS ratio
    FROM erp_products p,
         jsonb_array_elements(p.suit_singles) AS comp
    WHERE p.item_type = 1
      AND p.suit_singles IS NOT NULL
      AND p.active_status = 1
      AND comp->>'skuOuterId' IS NOT NULL
      AND comp->>'skuOuterId' != ''
),
sub_stock AS (
    SELECT
        org_id,
        sku_outer_id                AS sub_code,
        SUM(sellable_num)           AS total_sellable,
        SUM(total_stock)            AS total_stock,
        SUM(purchase_num)           AS total_onway
    FROM erp_stock_status
    WHERE sku_outer_id != ''
    GROUP BY org_id, sku_outer_id
),
kit_stock AS (
    SELECT
        kc.org_id,
        kc.kit_outer_id,
        kc.kit_sku_outer_id,
        MIN(FLOOR(COALESCE(ss.total_sellable, 0) / kc.ratio))::int  AS sellable_num,
        MIN(FLOOR(COALESCE(ss.total_stock, 0)    / kc.ratio))::int  AS total_stock,
        MIN(FLOOR(COALESCE(ss.total_onway, 0)    / kc.ratio))::int  AS purchase_num
    FROM kit_components kc
    LEFT JOIN sub_stock ss ON ss.sub_code = kc.sub_code AND ss.org_id IS NOT DISTINCT FROM kc.org_id
    GROUP BY kc.org_id, kc.kit_outer_id, kc.kit_sku_outer_id
)
SELECT
    ks.org_id,
    ks.kit_outer_id         AS outer_id,
    ks.kit_sku_outer_id     AS sku_outer_id,
    p.title                 AS item_name,
    ps.properties_name,
    ''::varchar             AS warehouse_id,
    ks.sellable_num,
    ks.total_stock,
    0                       AS lock_stock,
    ks.purchase_num,
    CASE
        WHEN ks.sellable_num <= 0 THEN 3
        WHEN ks.sellable_num < 10 THEN 2
        ELSE 1
    END                     AS stock_status
FROM kit_stock ks
LEFT JOIN erp_products p ON p.outer_id = ks.kit_outer_id AND p.org_id IS NOT DISTINCT FROM ks.org_id
LEFT JOIN erp_product_skus ps ON ps.sku_outer_id = ks.kit_sku_outer_id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_kit_stock
    ON mv_kit_stock (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), outer_id, sku_outer_id);

CREATE INDEX IF NOT EXISTS idx_mv_kit_stock_sku
    ON mv_kit_stock (sku_outer_id);

CREATE INDEX IF NOT EXISTS idx_mv_kit_stock_org
    ON mv_kit_stock (org_id) WHERE org_id IS NOT NULL;

-- ════════════════════════════════════════════════════════
-- E. organizations 表 updated_at 自动更新触发器
-- ════════════════════════════════════════════════════════

CREATE TRIGGER update_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

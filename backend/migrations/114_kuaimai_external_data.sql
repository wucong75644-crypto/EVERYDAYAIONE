-- ============================================================
-- 114: 快麦 Web 外部数据接入（智库 + viperp 销售主题）
--
-- 设计原则：
--   1. 通用业务字段全部建独立列（按真实 JSON 响应一字段一字段对照）
--   2. 动态字段（dl_* / dy_* / custom*）+ 完整原始 JSON → raw_payload JSONB
--   3. 字段一个不漏：业务列 + raw_payload 双重保险
--
-- 字段来源（基于 POC 真实响应）：
--   - 智库 /kmzk/profit/report/shop          → 338 字段 (98 通用 + 235 dl + 5 dy)
--   - viperp /report/.../list                → 62 字段 (56 通用 + 6 custom*)
--   - viperp /report/.../getFinanceAmount    → 2 字段 (amount + total)
--
-- 字段清单文件：tmp/thinktank_classified.md / tmp/viperp_list_fields.txt
-- ============================================================


-- ============================================================
-- 表 1: 凭证表
-- ============================================================
DROP TABLE IF EXISTS kuaimai_external_credentials CASCADE;
CREATE TABLE kuaimai_external_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    source VARCHAR(20) NOT NULL CHECK (source IN ('thinktank', 'viperp')),
    kuaimai_company_id INTEGER NOT NULL,

    -- Cookie 存储（短期明文，TODO: 接入 per-org encrypt_key）
    censeid_cookie TEXT NOT NULL,
    cookie_full TEXT,

    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'expired', 'invalid')),
    last_health_check_at TIMESTAMPTZ,
    last_sync_at TIMESTAMPTZ,
    last_sync_status VARCHAR(20),
    last_sync_error TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, source)
);
CREATE INDEX idx_kuaimai_creds_org ON kuaimai_external_credentials(org_id);
CREATE INDEX idx_kuaimai_creds_status ON kuaimai_external_credentials(status)
    WHERE status != 'invalid';


-- ============================================================
-- 表 2: 智库 - 店铺利润表
-- 真实字段：338 个（98 固定列 + 240 动态进 raw_payload）
-- ============================================================
DROP TABLE IF EXISTS erp_thinktank_profit_shop CASCADE;
CREATE TABLE erp_thinktank_profit_shop (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    kuaimai_company_id INTEGER NOT NULL,

    -- ───── 维度/标识字段（5 + 日期 1 + 其他字符串 4 = 10 个）─────
    shop_uni_id VARCHAR(64) NOT NULL,        -- "65109_900585629"
    "shopUniId" VARCHAR(64),                 -- 接口里同时存在两个字段名
    "shopName" VARCHAR(200),                 -- "BEAUTIFUL WISH旗舰店"
    "platformName" VARCHAR(50),              -- "拼多多"
    "platformIcon" TEXT,                     -- 图标 URL
    date_range DATE NOT NULL,                -- "2026-05-24"
    "time" DATE,                             -- 同 date_range
    "itemId" VARCHAR(32),                    -- "-1" = 全部商品
    "sortField" BIGINT,                      -- 2147483647
    insert_date BIGINT,                      -- 毫秒时间戳 1779552000000
    user_id INTEGER,                         -- 65109（=companyid）
    num INTEGER,                             -- 5
    trade_num INTEGER,                       -- 交易数 2

    -- ───── 售后退款系列（after_refund_*）─────
    after_refund NUMERIC(18,4),
    after_refund_cnt INTEGER,
    after_refund_cost NUMERIC(18,4),
    after_refund_cost_goods NUMERIC(18,4),
    after_refund_cost_logistics NUMERIC(18,4),
    after_refund_cost_only NUMERIC(18,4),
    after_refund_cost_sd NUMERIC(18,4),
    after_refund_goods NUMERIC(18,4),
    after_refund_logistics NUMERIC(18,4),
    after_refund_num INTEGER,
    after_refund_only NUMERIC(18,4),
    after_refund_sd NUMERIC(18,4),
    after_sale_payment NUMERIC(18,4),
    aftersale_refund NUMERIC(18,4),

    -- ───── 售中退款系列（onsale_refund_*）─────
    onsale_refund NUMERIC(18,4),
    onsale_refund_cnt INTEGER,
    onsale_refund_cost NUMERIC(18,4),
    onsale_refund_cost_sd NUMERIC(18,4),
    onsale_refund_num INTEGER,
    onsale_refund_sd NUMERIC(18,4),

    -- ───── 商品退款（sd_*: 商单/试单系列）─────
    sd NUMERIC(18,4),
    sd_charge NUMERIC(18,4),
    sd_cost NUMERIC(18,4),
    sd_num INTEGER,
    sd_trade_num INTEGER,
    sd_after_refund_cnt INTEGER,
    sd_after_refund_num INTEGER,
    sd_onsale_refund_cnt INTEGER,
    sd_onsale_refund_num INTEGER,
    send_cost NUMERIC(18,4),

    -- ───── cal_* 系列（计算项 = calculated_xxx_sd） ─────
    cal_after_refund_cost_sd NUMERIC(18,4),
    cal_after_refund_sd NUMERIC(18,4),
    cal_estimate_charge_sd NUMERIC(18,4),
    cal_onsale_refund_cost_sd NUMERIC(18,4),
    cal_onsale_refund_sd NUMERIC(18,4),
    cal_sd NUMERIC(18,4),
    cal_sd_cost NUMERIC(18,4),

    -- ───── no_cal_* 系列（未计算项）─────
    no_cal_after_refund_cost_sd NUMERIC(18,4),
    no_cal_after_refund_sd NUMERIC(18,4),
    no_cal_estimate_charge_sd NUMERIC(18,4),
    no_cal_onsale_refund_cost_sd NUMERIC(18,4),
    no_cal_onsale_refund_sd NUMERIC(18,4),
    no_cal_sd NUMERIC(18,4),
    no_cal_sd_cost NUMERIC(18,4),

    -- ───── 核心财务字段 ─────
    charge NUMERIC(18,4),                    -- 费用合计 16.09
    cost NUMERIC(18,4),                      -- 成本合计 14.05
    gcost NUMERIC(18,4),                     -- 货品成本 14.05
    item_cost NUMERIC(18,4),                 -- 商品成本 14.05
    estimate_charge NUMERIC(18,4),           -- 预估费用 3.20
    estimate_charge_sd NUMERIC(18,4),
    refund NUMERIC(18,4),                    -- 退款
    refund_cost NUMERIC(18,4),               -- 退款成本
    realsales NUMERIC(18,4),                 -- 实际销售 30.60
    incoming NUMERIC(18,4),                  -- 收入 30.60
    brand_new NUMERIC(18,4),

    -- ───── 利润率/汇总 ─────
    sum_gross_profit NUMERIC(18,4),          -- 总毛利 0.46
    sum_net_profit NUMERIC(18,4),            -- 总净利 16.55 ⭐
    profit_rate NUMERIC(10,4),               -- 利润率 0.02
    net_profit_rate NUMERIC(10,4),           -- 净利率 0.54
    sum_profit_rate VARCHAR(20),             -- "1.50%"（百分比字符串）
    sum_net_profit_rate VARCHAR(20),         -- "54.08%"

    -- ───── 平台费用项 ─────
    alipay_charge NUMERIC(18,4),
    card_pay_service NUMERIC(18,4),
    coupon_settle NUMERIC(18,4),
    ct_service_charge NUMERIC(18,4),
    daily_must_buy NUMERIC(18,4),
    dd_come_bao NUMERIC(18,4),
    donation NUMERIC(18,4),
    hb_pay_service NUMERIC(18,4),
    jishu_service_fee NUMERIC(18,4),
    jsfwf_hbfqmxyx NUMERIC(18,4),
    manage_charge NUMERIC(18,4),
    operator_charge NUMERIC(18,4),
    other_software_service NUMERIC(18,4),
    pdd_bill_charge NUMERIC(18,4),
    pdd_small_pay NUMERIC(18,4),
    platform_marketing_charge NUMERIC(18,4),
    qyhb_tb_cash_redpack NUMERIC(18,4),
    reight_insurance NUMERIC(18,4),
    return_integral NUMERIC(18,4),
    taoke_commission NUMERIC(18,4),
    taoke_refund NUMERIC(18,4),
    tianmao_pay_sjbzjlp NUMERIC(18,4),
    tmall_commission NUMERIC(18,4),
    tmall_integral NUMERIC(18,4),

    -- ───── 原始 JSON 留底（含 dl_*/dy_* 等所有 338 字段，一个不漏） ─────
    raw_payload JSONB NOT NULL,

    -- 同步元数据
    sync_batch_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (org_id, kuaimai_company_id, shop_uni_id, date_range)
);
CREATE INDEX idx_thinktank_profit_org_date ON erp_thinktank_profit_shop(org_id, date_range DESC);
CREATE INDEX idx_thinktank_profit_shop ON erp_thinktank_profit_shop(org_id, shop_uni_id);
CREATE INDEX idx_thinktank_profit_payload ON erp_thinktank_profit_shop USING gin(raw_payload);


-- ============================================================
-- 表 3: viperp - 销售主题报表
-- 真实字段：62 个（56 通用列 + 6 个 customXXX 进 raw_payload）
-- ============================================================
DROP TABLE IF EXISTS erp_viperp_sale_finance CASCADE;
CREATE TABLE erp_viperp_sale_finance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    kuaimai_company_id INTEGER NOT NULL,

    -- 维度（用户/店铺/平台/时间）
    user_id INTEGER NOT NULL,                            -- 900196946（这是店铺 userId）
    "userIds" VARCHAR(32),                               -- 备份字段
    "sysItemId" BIGINT,                                  -- -1 = 全部商品
    "sysSkuId" BIGINT,                                   -- 0
    "taobaoId" BIGINT,                                   -- 322981104
    "shopTitle" VARCHAR(200),                            -- "酸奶不吃鱼y"
    "shopNameWhole" VARCHAR(200),                        -- "酸奶不吃鱼y"
    "shopGroupName" VARCHAR(200),                        -- "廖晴宇"
    "shopLabel" VARCHAR(200),
    "shopSource" VARCHAR(50),                            -- "pdd"
    "shopSourceName" VARCHAR(50),                        -- "拼多多"
    "source" VARCHAR(50),                                -- "拼多多"
    "subSource" VARCHAR(50),                             -- "pdd"
    "supplierName" VARCHAR(200),                         -- "-"
    "supplierOuterId" VARCHAR(100),                      -- "-"
    "brand" VARCHAR(100),                                -- "未设置"
    "itemKind" VARCHAR(50),                              -- "-"
    "combineTypeDesc" VARCHAR(200),
    "extendShopInfo" TEXT,

    -- 时间范围（用同步时间段填充）
    date_range_start DATE NOT NULL,
    date_range_end DATE NOT NULL,

    -- 维度类型（默认 shop，未来扩展 sku/item/day/brand）
    dimension VARCHAR(20) NOT NULL DEFAULT 'shop',

    -- ───── 销售/数量类 ─────
    "actualPayAmount" NUMERIC(18,4),                     -- 实付金额 50343.86
    "actualPostFee" NUMERIC(18,4),                       -- 实际运费 12446.4
    "actualRefundItemCost" NUMERIC(18,4),
    "actualSysConsignCost" NUMERIC(18,4),                -- 实际发货成本 40266.21
    "actualSysConsignCount" INTEGER,
    "basePrice" NUMERIC(18,4),
    "cost" NUMERIC(18,4),
    "currentBaseRefundMoney" NUMERIC(18,4),
    "distributAmount" NUMERIC(18,4),
    "distributProfit" NUMERIC(18,4),
    "grossProfit" NUMERIC(18,4),                         -- 毛利润 10077.65
    "grossProfitRate" VARCHAR(20),                       -- "20.017600%"
    "grossProfitRateValue" NUMERIC(10,6),                -- 0.200176
    "itemCost" NUMERIC(18,4),                            -- 40266.21
    "itemCount" NUMERIC(18,4),                           -- 32449.0
    "marketPrice" NUMERIC(18,4),
    "payAmount" NUMERIC(18,4),
    "saleAvgPrice" NUMERIC(18,6),                        -- 销售均价 1.551476
    "saleMoney" NUMERIC(18,4),                           -- 销售额 50343.86
    "tradeCount" INTEGER,                                -- 交易数 6623
    "theoryPostFee" NUMERIC(18,4),                       -- 理论运费 12446.4

    -- ───── 运费系列 ─────
    "postFeeIn" NUMERIC(18,4),
    "postFeeInDutch" NUMERIC(18,4),
    "postFeeOut" NUMERIC(18,4),
    "postFeeOutDutch" NUMERIC(18,4),

    -- ───── 退款系列（raw* + refund*）─────
    "rawNotRefundItemMoney" NUMERIC(18,4),
    "rawRefundItemMoney" NUMERIC(18,4),
    "rawRefundMoney" NUMERIC(18,4),
    "rawRefundPostFee" NUMERIC(18,4),
    "refundBadItemCount" NUMERIC(18,4),
    "refundGoodItemCount" NUMERIC(18,4),                 -- 7258
    "refundItemCost" NUMERIC(18,4),                      -- 7265.126811
    "refundMoney" NUMERIC(18,4),
    "refundOutAfterItemCost" NUMERIC(18,4),              -- 1453.81
    "refundOutAfterItemMoney" NUMERIC(18,4),             -- 1292.05
    "refundOutBeforeItemCost" NUMERIC(18,4),             -- 5811.32
    "refundOutBeforeItemMoney" NUMERIC(18,4),            -- 10257.6
    "shippedRefundMoney" NUMERIC(18,4),

    -- ───── 汇总：amount + total（来自 getFinanceAmount 接口）─────
    summary_amount NUMERIC(18,4),                        -- 总金额
    summary_total INTEGER,                               -- 总数

    -- ───── 原始 JSON 留底（含 6 个 custom* 等所有 62 字段，一个不漏）─────
    raw_payload JSONB NOT NULL,

    sync_batch_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (org_id, kuaimai_company_id, user_id, dimension, date_range_start, date_range_end)
);
CREATE INDEX idx_viperp_sale_org_date ON erp_viperp_sale_finance(org_id, date_range_start DESC);
CREATE INDEX idx_viperp_sale_user ON erp_viperp_sale_finance(org_id, user_id);
CREATE INDEX idx_viperp_sale_payload ON erp_viperp_sale_finance USING gin(raw_payload);


-- ============================================================
-- 表 4: 同步日志
-- ============================================================
DROP TABLE IF EXISTS kuaimai_sync_logs CASCADE;
CREATE TABLE kuaimai_sync_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    source VARCHAR(20) NOT NULL,
    sync_type VARCHAR(20) NOT NULL
        CHECK (sync_type IN ('daily', 'manual', 'backfill')),

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'failed')),

    date_range_start DATE,
    date_range_end DATE,

    rows_synced INTEGER DEFAULT 0,
    error_message TEXT,
    metadata JSONB,                          -- 存 summary_amount/summary_total 等

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_sync_logs_org_started ON kuaimai_sync_logs(org_id, started_at DESC);
CREATE INDEX idx_sync_logs_source ON kuaimai_sync_logs(org_id, source, started_at DESC);


-- ============================================================
-- 表 5: 店铺 → 运营名 映射（自动同步）
-- 来源：每次 viperp sync 自动从 shopGroupName/shopTitle/shopSource 提取
--
-- 关键：这里只存"店铺归属哪个运营名"，不存企微账号
-- 企微账号绑定在 erp_operators 表（按运营名单独维护，避免重复存储）
--
-- 同步逻辑：
--   - 店铺换运营 → UPDATE operator_name + 推告警（不影响 erp_operators）
--   - 新店铺 → INSERT
--   - 店铺消失 → is_active=FALSE（保留历史）
-- ============================================================
DROP TABLE IF EXISTS erp_shop_operators CASCADE;
CREATE TABLE erp_shop_operators (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    kuaimai_company_id INTEGER NOT NULL,

    -- 店铺标识
    shop_user_id INTEGER NOT NULL,           -- viperp.userId
    shop_name VARCHAR(200),                  -- viperp.shopTitle
    platform_code VARCHAR(20),               -- viperp.shopSource: "pdd"/"taobao"/"douyin"
    platform_name VARCHAR(50),               -- viperp.shopSourceName: "拼多多"
    taobao_id BIGINT,                        -- viperp.taobaoId

    -- 当前归属的运营名（从 viperp.shopGroupName 提取，会随调整变化）
    operator_name VARCHAR(100),

    -- 同步追踪
    last_seen_in_sync UUID,
    last_seen_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (org_id, kuaimai_company_id, shop_user_id)
);
CREATE INDEX idx_shop_operators_org_active
    ON erp_shop_operators(org_id, is_active)
    WHERE is_active = TRUE;
CREATE INDEX idx_shop_operators_operator
    ON erp_shop_operators(org_id, operator_name)
    WHERE is_active = TRUE;
CREATE INDEX idx_shop_operators_platform
    ON erp_shop_operators(org_id, platform_code);


-- ============================================================
-- 表 5b: 运营名 → 企微账号 映射（管理员手动维护）
-- 关键设计：每个运营名只存一次，不随店铺调整重复
-- 用例：店铺归属变化时 join 这张表找新运营的企微账号
--
-- 同步逻辑：
--   - 检测到新运营名（erp_shop_operators 出现没见过的 operator_name）
--     → 自动 INSERT 一行 (operator_name, is_bound=FALSE) + 推告警让管理员绑定
--   - 运营离职 → 管理员手动设 is_active=FALSE
--   - 运营换企微号 → 管理员更新 wecom_userid
-- ============================================================
DROP TABLE IF EXISTS erp_operators CASCADE;
CREATE TABLE erp_operators (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    operator_name VARCHAR(100) NOT NULL,     -- "廖晴宇"

    -- 系统账号绑定（管理员手动）
    operator_user_id UUID,                   -- 关联 users.id（如果运营是系统用户）
    wecom_userid VARCHAR(64),                -- 企微 ID
    is_bound BOOLEAN NOT NULL DEFAULT FALSE, -- 是否已绑定企微

    -- 生命周期
    is_active BOOLEAN NOT NULL DEFAULT TRUE, -- 是否在职
    first_seen_at TIMESTAMPTZ DEFAULT NOW(), -- 第一次在数据里出现
    last_seen_at TIMESTAMPTZ,                -- 最近一次在数据里出现

    -- 绑定元数据
    bound_at TIMESTAMPTZ,
    bound_by UUID,                           -- 哪个管理员绑的
    notes TEXT,                              -- 备注（"已离职"/"兼任 xxx"）

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (org_id, operator_name)
);
CREATE INDEX idx_operators_org_active
    ON erp_operators(org_id, is_active)
    WHERE is_active = TRUE;
CREATE INDEX idx_operators_unbound
    ON erp_operators(org_id, is_bound)
    WHERE is_bound = FALSE AND is_active = TRUE;


-- ============================================================
-- 表 6: 字段/运营差异审计
-- 每次 sync 末尾跑 auditor，记录三类变化：
--   1. field_change: 快麦响应字段新增/消失/类型变化
--   2. operator_change: 店铺运营变动（换人）
--   3. shop_added / shop_removed: 店铺增删
-- 同时推送企微告警给 org owner（复用 erp_sync_healthcheck._push_to_org_admins 链路）
-- ============================================================
DROP TABLE IF EXISTS kuaimai_field_audit CASCADE;
CREATE TABLE kuaimai_field_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    source VARCHAR(20) NOT NULL,             -- thinktank / viperp
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 审计类型
    audit_type VARCHAR(30) NOT NULL          -- 决定 changes 字段语义
        CHECK (audit_type IN (
            'field_change',       -- 字段结构变化（new_fields/disappeared/type_changed）
            'operator_change',    -- 店铺运营变化（A 店铺：廖→张）
            'shop_added',         -- 新增店铺
            'shop_removed',       -- 店铺消失
            'new_operator'        -- 数据里出现新运营名（待管理员绑企微）
        )),

    -- 字段差异（audit_type='field_change' 时使用）
    new_fields JSONB DEFAULT '[]'::jsonb,
    disappeared_fields TEXT[] DEFAULT '{}',
    type_changed_fields JSONB DEFAULT '[]'::jsonb,
    all_fields_snapshot JSONB,

    -- 店铺/运营变化（audit_type='operator_change'/'shop_added'/'shop_removed' 时使用）
    -- 结构示例：
    --   operator_change: {"shop_user_id": 900xxx, "shop_name": "xxx", "old": "廖晴宇", "new": "张三"}
    --   shop_added:      {"shop_user_id": 900xxx, "shop_name": "xxx", "operator_name": "yyy", "platform": "pdd"}
    --   shop_removed:    {"shop_user_id": 900xxx, "shop_name": "xxx", "last_operator": "yyy"}
    changes JSONB DEFAULT '{}'::jsonb,

    -- 状态
    status VARCHAR(20) NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'acknowledged', 'migrated', 'ignored')),
    handled_by UUID,
    handled_at TIMESTAMPTZ,
    notes TEXT,

    -- 关联同步批次
    sync_batch_id UUID,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_field_audit_org_detected
    ON kuaimai_field_audit(org_id, detected_at DESC);
CREATE INDEX idx_field_audit_status
    ON kuaimai_field_audit(org_id, status)
    WHERE status = 'new';
CREATE INDEX idx_field_audit_type
    ON kuaimai_field_audit(org_id, audit_type, detected_at DESC);


-- ============================================================
-- 注释
-- ============================================================
COMMENT ON TABLE kuaimai_external_credentials IS
    '快麦 Web 接口凭证（管理员配置，按 org_id + source 唯一）';
COMMENT ON TABLE erp_thinktank_profit_shop IS
    '快麦智库 - 店铺利润表数据（338 字段：98 通用列 + raw_payload 含全部原始字段）';
COMMENT ON TABLE erp_viperp_sale_finance IS
    '快麦 viperp - 销售主题报表数据（62 字段：56 通用列 + raw_payload 含全部原始字段，含 6 个 custom*）';
COMMENT ON TABLE kuaimai_sync_logs IS
    '快麦外部数据同步日志';
COMMENT ON TABLE erp_shop_operators IS
    '店铺-运营名映射（自动从 viperp 同步，每次 sync UPSERT，店铺消失 is_active=FALSE）';
COMMENT ON TABLE erp_operators IS
    '运营-企微账号映射（管理员手动绑定，每个运营名只存一次，独立于店铺归属）';
COMMENT ON TABLE kuaimai_field_audit IS
    '快麦数据变化审计（字段/运营/店铺），自动推送企微告警给 org owner';

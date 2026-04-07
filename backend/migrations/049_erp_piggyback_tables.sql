-- 049: ERP 搭便车同步表（订单操作日志/包裹信息/售后操作日志/批次库存）

-- ── 订单操作日志 ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS erp_order_logs (
    id BIGSERIAL PRIMARY KEY,
    system_id VARCHAR(32) NOT NULL,
    operator VARCHAR(64),
    action VARCHAR(64),
    content TEXT,
    operate_time TIMESTAMP,
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erp_order_logs_sid ON erp_order_logs (system_id);
CREATE INDEX IF NOT EXISTS idx_erp_order_logs_org ON erp_order_logs (org_id);
CREATE INDEX IF NOT EXISTS idx_erp_order_logs_time ON erp_order_logs (operate_time DESC);
-- 同一订单同一时间同一操作去重
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_order_logs ON erp_order_logs (
    system_id, COALESCE(operate_time, '1970-01-01'), COALESCE(action, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000')
);

-- ── 订单包裹/快递信息 ────────────────────────────────────
CREATE TABLE IF NOT EXISTS erp_order_packages (
    id BIGSERIAL PRIMARY KEY,
    system_id VARCHAR(32) NOT NULL,
    package_id VARCHAR(64),
    express_no VARCHAR(64),
    express_company VARCHAR(64),
    express_company_code VARCHAR(32),
    items_json JSONB DEFAULT '[]',
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erp_order_packages_sid ON erp_order_packages (system_id);
CREATE INDEX IF NOT EXISTS idx_erp_order_packages_express ON erp_order_packages (express_no);
CREATE INDEX IF NOT EXISTS idx_erp_order_packages_org ON erp_order_packages (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_order_packages ON erp_order_packages (
    system_id, COALESCE(express_no, ''), COALESCE(package_id, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000')
);

-- ── 售后操作日志 ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS erp_aftersale_logs (
    id BIGSERIAL PRIMARY KEY,
    work_order_id VARCHAR(32) NOT NULL,
    operator VARCHAR(64),
    action VARCHAR(64),
    content TEXT,
    operate_time TIMESTAMP,
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erp_aftersale_logs_wid ON erp_aftersale_logs (work_order_id);
CREATE INDEX IF NOT EXISTS idx_erp_aftersale_logs_org ON erp_aftersale_logs (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_aftersale_logs ON erp_aftersale_logs (
    work_order_id, COALESCE(operate_time, '1970-01-01'), COALESCE(action, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000')
);

-- ── 批次效期库存 ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS erp_batch_stock (
    id BIGSERIAL PRIMARY KEY,
    outer_id VARCHAR(128) NOT NULL,
    sku_outer_id VARCHAR(128) NOT NULL DEFAULT '',
    item_name VARCHAR(256),
    batch_no VARCHAR(64),
    production_date VARCHAR(32),
    expiry_date VARCHAR(32),
    shelf_life_days INTEGER,
    stock_qty INTEGER NOT NULL DEFAULT 0,
    warehouse_name VARCHAR(128),
    shop_id VARCHAR(64),
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erp_batch_stock_outer ON erp_batch_stock (outer_id);
CREATE INDEX IF NOT EXISTS idx_erp_batch_stock_org ON erp_batch_stock (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_batch_stock ON erp_batch_stock (
    outer_id, sku_outer_id, COALESCE(batch_no, ''),
    COALESCE(shop_id, ''), COALESCE(warehouse_name, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000')
);

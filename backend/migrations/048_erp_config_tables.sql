-- 048: ERP 配置数据本地同步表（店铺/仓库/标签/分类/物流公司）
-- 低频全量同步，替代远程 API 读取

-- ── 店铺列表 ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS erp_shops (
    id BIGSERIAL PRIMARY KEY,
    shop_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    short_name VARCHAR(64),
    platform VARCHAR(32),
    nick VARCHAR(128),
    state SMALLINT NOT NULL DEFAULT 3,
    group_name VARCHAR(64),
    deadline VARCHAR(32),
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_shops ON erp_shops (shop_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_shops_org ON erp_shops (org_id);
CREATE INDEX IF NOT EXISTS idx_erp_shops_platform ON erp_shops (platform);
CREATE INDEX IF NOT EXISTS idx_erp_shops_state ON erp_shops (state);

-- ── 仓库列表 ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS erp_warehouses (
    id BIGSERIAL PRIMARY KEY,
    warehouse_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    code VARCHAR(64),
    warehouse_type SMALLINT NOT NULL DEFAULT 0,
    status SMALLINT NOT NULL DEFAULT 1,
    contact VARCHAR(64),
    contact_phone VARCHAR(32),
    province VARCHAR(32),
    city VARCHAR(32),
    district VARCHAR(32),
    address TEXT,
    is_virtual BOOLEAN NOT NULL DEFAULT FALSE,
    external_code VARCHAR(64),
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_warehouses ON erp_warehouses (warehouse_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_warehouses_org ON erp_warehouses (org_id);
CREATE INDEX IF NOT EXISTS idx_erp_warehouses_status ON erp_warehouses (status);

-- ── 标签列表（订单标签 + 商品标签统一存储）──────────────
CREATE TABLE IF NOT EXISTS erp_tags (
    id BIGSERIAL PRIMARY KEY,
    tag_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    tag_source VARCHAR(16) NOT NULL DEFAULT 'order',
    tag_type SMALLINT NOT NULL DEFAULT 0,
    color VARCHAR(16),
    remark TEXT,
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_tags ON erp_tags (tag_id, tag_source, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_tags_org ON erp_tags (org_id);
CREATE INDEX IF NOT EXISTS idx_erp_tags_source ON erp_tags (tag_source);

-- ── 分类列表（自定义分类 + 系统类目统一存储）──────────────
CREATE TABLE IF NOT EXISTS erp_categories (
    id BIGSERIAL PRIMARY KEY,
    cat_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    parent_name VARCHAR(128),
    full_name VARCHAR(256),
    cat_source VARCHAR(16) NOT NULL DEFAULT 'seller_cat',
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_categories ON erp_categories (cat_id, cat_source, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_categories_org ON erp_categories (org_id);

-- ── 物流公司列表 ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS erp_logistics_companies (
    id BIGSERIAL PRIMARY KEY,
    company_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    code VARCHAR(64),
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_logistics_companies ON erp_logistics_companies (company_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_logistics_companies_org ON erp_logistics_companies (org_id);

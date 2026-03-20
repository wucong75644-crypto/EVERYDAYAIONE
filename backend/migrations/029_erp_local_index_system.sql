-- 029: ERP数据本地索引系统 — 全部表结构 + 索引
-- 技术设计文档: docs/document/TECH_ERP数据本地索引系统.md
-- 包含: pg_trgm扩展 + 9张表 + 全部索引和约束

-- ============================================================
-- 任务1.0: 启用 pg_trgm 扩展（中文子串模糊搜索 GIN 索引依赖）
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- 任务1.1: erp_document_items（热数据 — 近3个月明细）
-- 统一存储所有单据类型的商品明细行，通过 doc_type 区分
-- ============================================================
CREATE TABLE IF NOT EXISTS erp_document_items (
    id              BIGSERIAL PRIMARY KEY,
    doc_type        VARCHAR(20) NOT NULL,           -- purchase/receipt/shelf/aftersale/order/purchase_return
    doc_id          VARCHAR(64) NOT NULL,           -- 单据ID
    doc_code        VARCHAR(64),                    -- 单据编号
    doc_status      VARCHAR(32),                    -- 单据状态
    doc_created_at  TIMESTAMP,                      -- 单据创建时间
    doc_modified_at TIMESTAMP,                      -- 单据最后修改时间
    item_index      SMALLINT NOT NULL DEFAULT 0,    -- 明细行序号（同一单据内）
    outer_id        VARCHAR(128),                   -- 主商家编码（SPU级）
    sku_outer_id    VARCHAR(128),                   -- SKU商家编码
    item_name       VARCHAR(256),                   -- 商品名称
    quantity        DECIMAL(12,2),                  -- 数量
    quantity_received DECIMAL(12,2),                -- 已到货数量（仅采购单）
    price           DECIMAL(12,2),                  -- 单价
    amount          DECIMAL(12,2),                  -- 金额
    supplier_name   VARCHAR(128),                   -- 供应商名称（采购/收货/采退）
    warehouse_name  VARCHAR(128),                   -- 仓库名称
    shop_name       VARCHAR(128),                   -- 店铺名称（售后/订单）
    platform        VARCHAR(20),                    -- 来源平台（tb/jd/pdd/dy/xhs/1688等）
    order_no        VARCHAR(64),                    -- 平台订单号tid（订单+售后共用）
    order_status    VARCHAR(32),                    -- 订单系统状态（仅订单）
    express_no      VARCHAR(64),                    -- 快递单号（仅订单）
    express_company VARCHAR(64),                    -- 快递公司（仅订单）
    cost            DECIMAL(12,2),                  -- 成本价（仅订单子商品）
    pay_time        TIMESTAMP,                      -- 支付时间（仅订单）
    consign_time    TIMESTAMP,                      -- 发货时间（仅订单）
    refund_status   VARCHAR(32),                    -- 退款状态（仅订单子商品）
    discount_fee    DECIMAL(12,2),                  -- 折扣金额（仅订单，按比例均摊，尾差兜底）
    post_fee        DECIMAL(12,2),                  -- 运费（仅订单，仅item_index=0首行存值）
    gross_profit    DECIMAL(12,2),                  -- 毛利（仅订单，仅item_index=0首行存值）
    aftersale_type  SMALLINT,                       -- 售后类型（0~9，见设计文档）
    refund_money    DECIMAL(12,2),                  -- 系统退款金额（仅售后）
    raw_refund_money DECIMAL(12,2),                 -- 平台实退金额（仅售后）
    text_reason     VARCHAR(256),                   -- 售后原因（仅售后）
    finished_at     TIMESTAMP,                      -- 完结时间（仅售后）
    real_qty        DECIMAL(12,2),                  -- 实退数量（仅售后商品）
    delivery_date   TIMESTAMP,                      -- 交货日期（仅采购）
    actual_return_qty DECIMAL(12,2),                -- 实退数量（仅采退）
    purchase_order_code VARCHAR(64),                -- 关联采购单号（收货/采退）
    remark          TEXT,                           -- 单据/行备注
    sys_memo        TEXT,                           -- 系统备注（仅订单）
    buyer_message   TEXT,                           -- 买家留言（仅订单）
    creator_name    VARCHAR(64),                    -- 创建人（采购/收货/采退）
    extra_json      JSONB DEFAULT '{}',             -- 扩展字段
    synced_at       TIMESTAMP NOT NULL DEFAULT NOW() -- 同步时间
);

-- 热表索引（14个）
CREATE INDEX IF NOT EXISTS idx_doc_items_outer_id ON erp_document_items (outer_id);
CREATE INDEX IF NOT EXISTS idx_doc_items_sku_outer_id ON erp_document_items (sku_outer_id);
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_type_outer ON erp_document_items (doc_type, outer_id, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_type_sku ON erp_document_items (doc_type, sku_outer_id, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_id ON erp_document_items (doc_type, doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_items_modified ON erp_document_items (doc_modified_at);
CREATE INDEX IF NOT EXISTS idx_doc_items_platform ON erp_document_items (platform) WHERE platform IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_shop ON erp_document_items (shop_name, doc_type);
CREATE INDEX IF NOT EXISTS idx_doc_items_order_no ON erp_document_items (order_no) WHERE order_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_refund ON erp_document_items (refund_status) WHERE refund_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_consign ON erp_document_items (consign_time) WHERE consign_time IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_aftersale_type ON erp_document_items (aftersale_type) WHERE aftersale_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_finished ON erp_document_items (finished_at) WHERE finished_at IS NOT NULL;
-- 唯一约束（upsert依赖）
CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_items ON erp_document_items (doc_type, doc_id, item_index);

COMMENT ON TABLE erp_document_items IS 'ERP单据明细热表（近3个月），统一存储采购/收货/上架/售后/订单/采退的商品明细行';

-- ============================================================
-- 任务1.2: erp_document_items_archive（冷归档 — 3个月前明细）
-- 与热表完全相同结构，存放归档数据
-- ============================================================
CREATE TABLE IF NOT EXISTS erp_document_items_archive (
    id              BIGSERIAL PRIMARY KEY,
    doc_type        VARCHAR(20) NOT NULL,
    doc_id          VARCHAR(64) NOT NULL,
    doc_code        VARCHAR(64),
    doc_status      VARCHAR(32),
    doc_created_at  TIMESTAMP,
    doc_modified_at TIMESTAMP,
    item_index      SMALLINT NOT NULL DEFAULT 0,
    outer_id        VARCHAR(128),
    sku_outer_id    VARCHAR(128),
    item_name       VARCHAR(256),
    quantity        DECIMAL(12,2),
    quantity_received DECIMAL(12,2),
    price           DECIMAL(12,2),
    amount          DECIMAL(12,2),
    supplier_name   VARCHAR(128),
    warehouse_name  VARCHAR(128),
    shop_name       VARCHAR(128),
    platform        VARCHAR(20),
    order_no        VARCHAR(64),
    order_status    VARCHAR(32),
    express_no      VARCHAR(64),
    express_company VARCHAR(64),
    cost            DECIMAL(12,2),
    pay_time        TIMESTAMP,
    consign_time    TIMESTAMP,
    refund_status   VARCHAR(32),
    discount_fee    DECIMAL(12,2),
    post_fee        DECIMAL(12,2),
    gross_profit    DECIMAL(12,2),
    aftersale_type  SMALLINT,
    refund_money    DECIMAL(12,2),
    raw_refund_money DECIMAL(12,2),
    text_reason     VARCHAR(256),
    finished_at     TIMESTAMP,
    real_qty        DECIMAL(12,2),
    delivery_date   TIMESTAMP,
    actual_return_qty DECIMAL(12,2),
    purchase_order_code VARCHAR(64),
    remark          TEXT,
    sys_memo        TEXT,
    buyer_message   TEXT,
    creator_name    VARCHAR(64),
    extra_json      JSONB DEFAULT '{}',
    synced_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 冷表索引（与热表一致 + 唯一约束保证归档幂等）
CREATE INDEX IF NOT EXISTS idx_archive_items_outer_id ON erp_document_items_archive (outer_id);
CREATE INDEX IF NOT EXISTS idx_archive_items_sku_outer_id ON erp_document_items_archive (sku_outer_id);
CREATE INDEX IF NOT EXISTS idx_archive_items_doc_type_outer ON erp_document_items_archive (doc_type, outer_id, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_archive_items_doc_type_sku ON erp_document_items_archive (doc_type, sku_outer_id, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_archive_items_doc_id ON erp_document_items_archive (doc_type, doc_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_archive_items ON erp_document_items_archive (doc_type, doc_id, item_index);

COMMENT ON TABLE erp_document_items_archive IS 'ERP单据明细冷表（归档，3个月前数据）';

-- ============================================================
-- 任务1.3: erp_product_daily_stats（聚合层 — 每日单品统计，永久保留）
-- 聚合规则: *_count = COUNT(DISTINCT doc_id), *_qty = SUM(quantity)
-- ============================================================
CREATE TABLE IF NOT EXISTS erp_product_daily_stats (
    id              BIGSERIAL PRIMARY KEY,
    stat_date       DATE NOT NULL,                  -- 统计日期 = doc_created_at::date
    outer_id        VARCHAR(128) NOT NULL,          -- 主商家编码
    sku_outer_id    VARCHAR(128),                   -- SKU编码（NULL=SPU级汇总）
    item_name       VARCHAR(256),                   -- 商品名称（冗余）
    -- 采购
    purchase_count          INTEGER NOT NULL DEFAULT 0,
    purchase_qty            DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_received_qty   DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_amount         DECIMAL(12,2) NOT NULL DEFAULT 0,
    -- 收货
    receipt_count           INTEGER NOT NULL DEFAULT 0,
    receipt_qty             DECIMAL(12,2) NOT NULL DEFAULT 0,
    -- 上架
    shelf_count             INTEGER NOT NULL DEFAULT 0,
    shelf_qty               DECIMAL(12,2) NOT NULL DEFAULT 0,
    -- 采退
    purchase_return_count   INTEGER NOT NULL DEFAULT 0,
    purchase_return_qty     DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_return_amount  DECIMAL(12,2) NOT NULL DEFAULT 0,
    -- 售后（按类型细分）
    aftersale_count         INTEGER NOT NULL DEFAULT 0,     -- 仅含有商品明细的工单
    aftersale_refund_count  INTEGER NOT NULL DEFAULT 0,     -- 仅退款（type=1,5）
    aftersale_return_count  INTEGER NOT NULL DEFAULT 0,     -- 退货（type=2）
    aftersale_exchange_count INTEGER NOT NULL DEFAULT 0,    -- 换货（type=4）
    aftersale_reissue_count INTEGER NOT NULL DEFAULT 0,     -- 补发（type=3）
    aftersale_reject_count  INTEGER NOT NULL DEFAULT 0,     -- 拒收退货（type=7）
    aftersale_repair_count  INTEGER NOT NULL DEFAULT 0,     -- 维修（type=9）
    aftersale_other_count   INTEGER NOT NULL DEFAULT 0,     -- 其他（type=0,8）
    aftersale_qty           DECIMAL(12,2) NOT NULL DEFAULT 0,
    aftersale_amount        DECIMAL(12,2) NOT NULL DEFAULT 0,
    -- 订单
    order_count             INTEGER NOT NULL DEFAULT 0,
    order_qty               DECIMAL(12,2) NOT NULL DEFAULT 0,
    order_amount            DECIMAL(12,2) NOT NULL DEFAULT 0,
    order_shipped_count     INTEGER NOT NULL DEFAULT 0,     -- consign_time IS NOT NULL
    order_finished_count    INTEGER NOT NULL DEFAULT 0,     -- order_status = 'FINISHED'
    order_refund_count      INTEGER NOT NULL DEFAULT 0,     -- refund_status IS NOT NULL
    order_cancelled_count   INTEGER NOT NULL DEFAULT 0,     -- extra_json->>'isCancel' = '1'
    order_cost              DECIMAL(12,2) NOT NULL DEFAULT 0, -- SUM(cost * quantity)
    updated_at              TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 唯一约束（COALESCE处理NULL，保证SPU级upsert正确）
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_stats ON erp_product_daily_stats (stat_date, outer_id, COALESCE(sku_outer_id, ''));
CREATE INDEX IF NOT EXISTS idx_daily_stats_outer ON erp_product_daily_stats (outer_id, stat_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON erp_product_daily_stats (stat_date);

COMMENT ON TABLE erp_product_daily_stats IS 'ERP每日单品聚合统计（永久保留），聚合count用COUNT(DISTINCT doc_id)';

-- ============================================================
-- 任务1.4: erp_products（商品主数据 SPU级）+ erp_product_skus（SKU级）
-- ============================================================
CREATE TABLE IF NOT EXISTS erp_products (
    id              BIGSERIAL PRIMARY KEY,
    outer_id        VARCHAR(128) UNIQUE NOT NULL,   -- 主商家编码
    title           VARCHAR(256),                   -- 商品名称
    item_type       SMALLINT NOT NULL DEFAULT 0,    -- 0=普通,1=SKU套件,2=纯套件,3=包材
    is_virtual      BOOLEAN NOT NULL DEFAULT false, -- 是否虚拟商品
    active_status   SMALLINT NOT NULL DEFAULT 1,    -- 1=启用,0=停用,-1=已删除
    barcode         VARCHAR(64),                    -- 商品条码
    purchase_price  DECIMAL(12,2),                  -- 采购价
    selling_price   DECIMAL(12,2),                  -- 销售价
    market_price    DECIMAL(12,2),                  -- 市场价
    weight          DECIMAL(10,3),                  -- 重量(Kg)
    unit            VARCHAR(16),                    -- 单位
    is_gift         BOOLEAN NOT NULL DEFAULT false,  -- 是否赠品
    sys_item_id     VARCHAR(64),                    -- 系统商品ID
    brand           VARCHAR(64),                    -- 品牌
    shipper         VARCHAR(128),                   -- 货主名称
    remark          TEXT,                           -- 商品备注（HTML清洗后）
    created_at      TIMESTAMP,                      -- 商品创建时间
    modified_at     TIMESTAMP,                      -- 商品更新时间
    pic_url         VARCHAR(512),                   -- 商品主图URL
    suit_singles    JSONB,                          -- 套件子单品列表（仅套件）
    extra_json      JSONB DEFAULT '{}',             -- 扩展字段
    synced_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_products_barcode ON erp_products (barcode) WHERE barcode IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_title ON erp_products USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_products_type ON erp_products (item_type);
CREATE INDEX IF NOT EXISTS idx_products_shipper ON erp_products (shipper) WHERE shipper IS NOT NULL;

COMMENT ON TABLE erp_products IS 'ERP商品主数据（SPU级），title使用pg_trgm GIN索引支持中文子串搜索';

CREATE TABLE IF NOT EXISTS erp_product_skus (
    id              BIGSERIAL PRIMARY KEY,
    outer_id        VARCHAR(128) NOT NULL,          -- 所属商品主编码
    sku_outer_id    VARCHAR(128) UNIQUE NOT NULL,   -- SKU商家编码
    properties_name VARCHAR(256),                   -- 规格属性
    barcode         VARCHAR(64),                    -- SKU条码
    purchase_price  DECIMAL(12,2),                  -- SKU采购价
    selling_price   DECIMAL(12,2),                  -- SKU销售价
    market_price    DECIMAL(12,2),                  -- SKU市场价
    weight          DECIMAL(10,3),                  -- 重量(Kg)
    unit            VARCHAR(16),                    -- 单位
    shipper         VARCHAR(128),                   -- 货主名称
    pic_url         VARCHAR(512),                   -- SKU图片URL
    sys_sku_id      VARCHAR(64),                    -- 系统SKU ID
    active_status   SMALLINT NOT NULL DEFAULT 1,    -- 1=启用,0=停用
    extra_json      JSONB DEFAULT '{}',             -- 扩展字段
    synced_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_skus_outer_id ON erp_product_skus (outer_id);
CREATE INDEX IF NOT EXISTS idx_skus_barcode ON erp_product_skus (barcode) WHERE barcode IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_skus_properties_name ON erp_product_skus USING GIN (properties_name gin_trgm_ops);

COMMENT ON TABLE erp_product_skus IS 'ERP商品SKU明细，properties_name使用pg_trgm GIN索引支持中文规格搜索';

-- ============================================================
-- 任务1.5: erp_stock_status（库存快照 — SKU级实时库存）
-- ============================================================
CREATE TABLE IF NOT EXISTS erp_stock_status (
    id              BIGSERIAL PRIMARY KEY,
    outer_id        VARCHAR(128) NOT NULL,          -- 主商家编码
    sku_outer_id    VARCHAR(128) NOT NULL DEFAULT '', -- SKU编码（''=SPU级汇总行）
    item_name       VARCHAR(256),                   -- 商品名称（冗余）
    properties_name VARCHAR(256),                   -- 规格属性
    total_stock     DECIMAL(12,2) NOT NULL DEFAULT 0,
    sellable_num    DECIMAL(12,2) NOT NULL DEFAULT 0,
    available_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
    lock_stock      DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_num    DECIMAL(12,2) NOT NULL DEFAULT 0,   -- 采购在途
    on_the_way_num  DECIMAL(12,2) NOT NULL DEFAULT 0,   -- 销退在途
    defective_stock DECIMAL(12,2) NOT NULL DEFAULT 0,   -- 残次品
    virtual_stock   DECIMAL(12,2) NOT NULL DEFAULT 0,
    stock_status    SMALLINT NOT NULL DEFAULT 0,    -- 1=正常,2=警戒,3=无货,4=超卖,6=有货
    purchase_price  DECIMAL(12,2),
    selling_price   DECIMAL(12,2),
    market_price    DECIMAL(12,2),
    allocate_num    DECIMAL(12,2) NOT NULL DEFAULT 0,   -- 调拨在途
    refund_stock    DECIMAL(12,2) NOT NULL DEFAULT 0,   -- 退款库存
    purchase_stock  DECIMAL(12,2) NOT NULL DEFAULT 0,   -- 入库暂存
    supplier_codes  VARCHAR(256),                   -- 关联供应商编码
    supplier_names  VARCHAR(256),                   -- 关联供应商名称
    warehouse_id    VARCHAR(64),                    -- 仓库ID
    stock_modified_time TIMESTAMP,                  -- 库存更新时间
    extra_json      JSONB DEFAULT '{}',
    synced_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_outer_sku ON erp_stock_status (outer_id, sku_outer_id);
CREATE INDEX IF NOT EXISTS idx_stock_outer_id ON erp_stock_status (outer_id);
CREATE INDEX IF NOT EXISTS idx_stock_sku_outer_id ON erp_stock_status (sku_outer_id) WHERE sku_outer_id != '';
CREATE INDEX IF NOT EXISTS idx_stock_status ON erp_stock_status (stock_status);
CREATE INDEX IF NOT EXISTS idx_stock_sellable ON erp_stock_status (sellable_num);
CREATE INDEX IF NOT EXISTS idx_stock_warehouse ON erp_stock_status (warehouse_id) WHERE warehouse_id IS NOT NULL;

COMMENT ON TABLE erp_stock_status IS 'ERP库存快照（SKU级），~1分钟同步一次';

-- ============================================================
-- 任务1.6: erp_suppliers（供应商主数据）
-- ============================================================
CREATE TABLE IF NOT EXISTS erp_suppliers (
    id              BIGSERIAL PRIMARY KEY,
    code            VARCHAR(64) UNIQUE NOT NULL,    -- 供应商编码
    name            VARCHAR(128) NOT NULL,          -- 供应商名称
    status          SMALLINT NOT NULL DEFAULT 1,    -- 1=启用,0=停用
    contact_name    VARCHAR(64),
    mobile          VARCHAR(32),
    phone           VARCHAR(32),
    email           VARCHAR(128),
    category_name   VARCHAR(64),                    -- 供应商分类
    bill_type       VARCHAR(32),                    -- 结算方式
    plan_receive_day INTEGER,                       -- 预计交期(天)
    address         TEXT,
    remark          TEXT,
    synced_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_suppliers_name ON erp_suppliers (name);
CREATE INDEX IF NOT EXISTS idx_suppliers_status ON erp_suppliers (status);

COMMENT ON TABLE erp_suppliers IS 'ERP供应商主数据，全量覆盖同步';

-- ============================================================
-- 任务1.7: erp_product_platform_map（平台商品↔ERP编码映射）
-- ============================================================
CREATE TABLE IF NOT EXISTS erp_product_platform_map (
    id              BIGSERIAL PRIMARY KEY,
    outer_id        VARCHAR(128) NOT NULL,          -- ERP主商家编码
    num_iid         VARCHAR(64) NOT NULL,           -- 平台商品ID
    user_id         VARCHAR(64),                    -- 店铺ID
    title           VARCHAR(256),                   -- 平台商品名称
    sku_mappings    JSONB DEFAULT '[]',             -- SKU映射列表
    synced_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_map ON erp_product_platform_map (outer_id, num_iid);
CREATE INDEX IF NOT EXISTS idx_platform_map_outer ON erp_product_platform_map (outer_id);
CREATE INDEX IF NOT EXISTS idx_platform_map_numiid ON erp_product_platform_map (num_iid);
CREATE INDEX IF NOT EXISTS idx_platform_map_user ON erp_product_platform_map (user_id);

COMMENT ON TABLE erp_product_platform_map IS 'ERP编码↔平台商品ID映射，下架检查用，每6小时同步';

-- ============================================================
-- 任务1.8: erp_sync_state（同步状态追踪）
-- ============================================================
CREATE TABLE IF NOT EXISTS erp_sync_state (
    id              SERIAL PRIMARY KEY,
    sync_type       VARCHAR(20) UNIQUE NOT NULL,    -- purchase/receipt/shelf/aftersale/order/purchase_return/product/stock/supplier/platform_map/archive/stats
    last_sync_time  TIMESTAMP,                      -- 上次成功同步的数据截止时间
    last_run_at     TIMESTAMP,                      -- 上次运行时间
    error_count     SMALLINT NOT NULL DEFAULT 0,    -- 连续失败次数（成功归零）
    last_error      TEXT,                           -- 上次错误信息（成功时清空）
    total_synced    INTEGER NOT NULL DEFAULT 0,     -- 累计同步记录数
    status          VARCHAR(16) NOT NULL DEFAULT 'idle', -- idle/running/error
    is_initial_done BOOLEAN NOT NULL DEFAULT false  -- 首次全量同步是否完成
);

COMMENT ON TABLE erp_sync_state IS 'ERP同步状态追踪，每种sync_type一行，含健康监控字段';

-- ============================================================
-- DB 锁降级用 RPC 函数（原子 CAS，避免 TOCTOU 竞态）
-- 当 Redis 不可用时，Worker 通过此函数抢锁
-- ============================================================
CREATE OR REPLACE FUNCTION erp_try_acquire_sync_lock(p_lock_ttl_seconds INT DEFAULT 300)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_acquired BOOLEAN;
BEGIN
    -- 原子 CAS：仅当无人持锁或锁已超时时才更新
    UPDATE erp_sync_state
    SET status = 'running', last_run_at = NOW()
    WHERE sync_type = 'purchase'  -- 用 purchase 行作为全局锁标记
      AND (
          status != 'running'
          OR last_run_at < NOW() - (p_lock_ttl_seconds || ' seconds')::INTERVAL
      );

    GET DIAGNOSTICS v_acquired = ROW_COUNT;
    RETURN v_acquired > 0;
END;
$$;

-- ============================================================
-- 聚合计算 RPC：对指定 (outer_id, stat_date) 全量重算 daily_stats
-- 聚合规则：*_count = COUNT(DISTINCT doc_id)（设计文档 G1）
-- ============================================================
CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats(
    p_outer_id VARCHAR,
    p_stat_date DATE
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO erp_product_daily_stats (
        stat_date, outer_id, sku_outer_id, item_name,
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
        NULL,  -- SPU 级汇总
        MAX(item_name),
        -- 采购
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(quantity_received) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase'), 0),
        -- 收货
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'receipt'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'receipt'), 0),
        -- 上架
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'shelf'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'shelf'), 0),
        -- 采退
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase_return'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase_return'), 0),
        -- 售后
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
        -- 订单
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
    ON CONFLICT (stat_date, outer_id, COALESCE(sku_outer_id, ''))
    DO UPDATE SET
        item_name = EXCLUDED.item_name,
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

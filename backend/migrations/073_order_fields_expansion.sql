-- 073: 订单/商品/SKU 表字段扩展
-- 订单表：标记字段从 extra_json 提升为独立列 + 买家/收件人/规格/缺货
-- 商品表：多规格标识 + 尺寸
-- SKU表：尺寸

BEGIN;

-- ── 订单表 erp_document_items：标记字段（原存 extra_json） ──────────

ALTER TABLE erp_document_items
    ADD COLUMN IF NOT EXISTS order_type    SMALLINT,
    ADD COLUMN IF NOT EXISTS pay_amount    DECIMAL(12,2),
    ADD COLUMN IF NOT EXISTS is_cancel     BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_refund     BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_exception  BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_halt       BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_urgent     BOOLEAN;

-- ── 订单表：买家 + 收件人信息 ──────────────────────────────

ALTER TABLE erp_document_items
    ADD COLUMN IF NOT EXISTS buyer_nick         VARCHAR(128),
    ADD COLUMN IF NOT EXISTS receiver_name      VARCHAR(64),
    ADD COLUMN IF NOT EXISTS receiver_mobile    VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_phone     VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_state     VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_city      VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_district  VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_address   VARCHAR(512);

-- ── 订单表：状态名 + 子订单级字段 ──────────────────────────

ALTER TABLE erp_document_items
    ADD COLUMN IF NOT EXISTS status_name         VARCHAR(32),
    ADD COLUMN IF NOT EXISTS sku_properties_name VARCHAR(256),
    ADD COLUMN IF NOT EXISTS diff_stock_num      DECIMAL(12,2);

-- ── 订单表索引 ──────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_doc_items_buyer
    ON erp_document_items (buyer_nick) WHERE buyer_nick IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_receiver_mobile
    ON erp_document_items (receiver_mobile) WHERE receiver_mobile IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_is_exception
    ON erp_document_items (is_exception) WHERE is_exception = true;
CREATE INDEX IF NOT EXISTS idx_doc_items_is_halt
    ON erp_document_items (is_halt) WHERE is_halt = true;
CREATE INDEX IF NOT EXISTS idx_doc_items_order_type
    ON erp_document_items (order_type) WHERE order_type IS NOT NULL;

-- ── 归档表同步加列 ────────────────────────────────────

ALTER TABLE erp_document_items_archive
    ADD COLUMN IF NOT EXISTS order_type    SMALLINT,
    ADD COLUMN IF NOT EXISTS pay_amount    DECIMAL(12,2),
    ADD COLUMN IF NOT EXISTS is_cancel     BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_refund     BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_exception  BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_halt       BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_urgent     BOOLEAN,
    ADD COLUMN IF NOT EXISTS buyer_nick         VARCHAR(128),
    ADD COLUMN IF NOT EXISTS receiver_name      VARCHAR(64),
    ADD COLUMN IF NOT EXISTS receiver_mobile    VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_phone     VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_state     VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_city      VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_district  VARCHAR(32),
    ADD COLUMN IF NOT EXISTS receiver_address   VARCHAR(512),
    ADD COLUMN IF NOT EXISTS status_name         VARCHAR(32),
    ADD COLUMN IF NOT EXISTS sku_properties_name VARCHAR(256),
    ADD COLUMN IF NOT EXISTS diff_stock_num      DECIMAL(12,2);

-- ── 商品表 erp_products：多规格标识 + 尺寸 ───────────────

ALTER TABLE erp_products
    ADD COLUMN IF NOT EXISTS is_sku_item BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS length      DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS width       DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS height      DECIMAL(10,2);

-- ── SKU表 erp_product_skus：尺寸 ────────────────────────

ALTER TABLE erp_product_skus
    ADD COLUMN IF NOT EXISTS length DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS width  DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS height DECIMAL(10,2);

-- ── 回填：从 extra_json 提取已有数据到新独立字段 ─────────

UPDATE erp_document_items
SET
    order_type   = (extra_json->>'type')::SMALLINT,
    pay_amount   = (extra_json->>'payAmount')::DECIMAL(12,2),
    is_cancel    = CASE WHEN (extra_json->>'isCancel')::INT = 1 THEN true ELSE false END,
    is_refund    = CASE WHEN (extra_json->>'isRefund')::INT = 1 THEN true ELSE false END,
    is_exception = CASE WHEN (extra_json->>'isExcep')::INT = 1 THEN true ELSE false END,
    is_halt      = CASE WHEN (extra_json->>'isHalt')::INT = 1 THEN true ELSE false END,
    is_urgent    = CASE WHEN (extra_json->>'isUrgent')::INT = 1 THEN true ELSE false END
WHERE doc_type = 'order'
  AND extra_json IS NOT NULL
  AND extra_json != '{}'::jsonb;

COMMIT;

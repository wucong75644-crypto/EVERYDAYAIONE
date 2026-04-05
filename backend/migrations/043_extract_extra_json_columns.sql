-- ============================================================
-- 043: 从 extra_json 提取有价值字段为独立列
--
-- 订单(order): 7 个字段
-- 售后(aftersale): 8 个字段
-- ============================================================

-- ════════════════════════════════════════════
-- A. 订单字段
-- ════════════════════════════════════════════

-- 实付总额（订单级，区别于行级 amount）
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS pay_amount NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS pay_amount NUMERIC(12,2);

-- 标志位（0/1）
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_cancel SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_cancel SMALLINT;

ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_refund SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_refund SMALLINT;

ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_exception SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_exception SMALLINT;

ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_halt SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_halt SMALLINT;

ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_urgent SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_urgent SMALLINT;

-- ════════════════════════════════════════════
-- B. 售后字段
-- ════════════════════════════════════════════

-- 商品状态（良品/残品）
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS good_status VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS good_status VARCHAR(32);

-- 退货物流
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS refund_warehouse_name VARCHAR(128);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS refund_warehouse_name VARCHAR(128);

ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS refund_express_company VARCHAR(64);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS refund_express_company VARCHAR(64);

ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS refund_express_no VARCHAR(64);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS refund_express_no VARCHAR(64);

-- 补发关联
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS reissue_sid VARCHAR(64);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS reissue_sid VARCHAR(64);

-- 平台退款单号
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS platform_refund_id VARCHAR(64);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS platform_refund_id VARCHAR(64);

-- 售后短ID
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS short_id VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS short_id VARCHAR(32);

-- 良品/残次品数量
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS good_item_count NUMERIC(10,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS good_item_count NUMERIC(10,2);

ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS bad_item_count NUMERIC(10,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS bad_item_count NUMERIC(10,2);

-- ════════════════════════════════════════════
-- C. 回填历史数据
-- ════════════════════════════════════════════

-- C1. 订单回填
UPDATE erp_document_items SET
  pay_amount = (extra_json->>'payAmount')::NUMERIC,
  is_cancel = (extra_json->>'isCancel')::SMALLINT,
  is_refund = (extra_json->>'isRefund')::SMALLINT,
  is_exception = (extra_json->>'isExcep')::SMALLINT,
  is_halt = (extra_json->>'isHalt')::SMALLINT,
  is_urgent = (extra_json->>'isUrgent')::SMALLINT
WHERE doc_type = 'order'
  AND extra_json IS NOT NULL
  AND pay_amount IS NULL;

-- C2. 售后回填
UPDATE erp_document_items SET
  good_status = extra_json->>'goodStatus',
  refund_warehouse_name = extra_json->>'refundWarehouseName',
  refund_express_company = extra_json->>'refundExpressCompany',
  refund_express_no = extra_json->>'refundExpressId',
  reissue_sid = extra_json->>'reissueSid',
  platform_refund_id = extra_json->>'platformId',
  short_id = extra_json->>'shortId',
  good_item_count = (extra_json->>'goodItemCount')::NUMERIC,
  bad_item_count = (extra_json->>'badItemCount')::NUMERIC
WHERE doc_type = 'aftersale'
  AND extra_json IS NOT NULL
  AND good_status IS NULL;

-- ════════════════════════════════════════════
-- D. 注释
-- ════════════════════════════════════════════
COMMENT ON COLUMN erp_document_items.pay_amount IS '订单实付总额（订单级）';
COMMENT ON COLUMN erp_document_items.is_cancel IS '是否取消(0/1)';
COMMENT ON COLUMN erp_document_items.is_refund IS '是否退款(0/1)';
COMMENT ON COLUMN erp_document_items.is_exception IS '是否异常(0/1)';
COMMENT ON COLUMN erp_document_items.is_halt IS '是否挂起(0/1)';
COMMENT ON COLUMN erp_document_items.is_urgent IS '是否加急(0/1)';
COMMENT ON COLUMN erp_document_items.good_status IS '售后商品状态(良品/残品)';
COMMENT ON COLUMN erp_document_items.refund_warehouse_name IS '退货仓库名称';
COMMENT ON COLUMN erp_document_items.refund_express_company IS '退货快递公司';
COMMENT ON COLUMN erp_document_items.refund_express_no IS '退货快递单号';
COMMENT ON COLUMN erp_document_items.reissue_sid IS '补发系统单号';
COMMENT ON COLUMN erp_document_items.platform_refund_id IS '平台退款单号';
COMMENT ON COLUMN erp_document_items.short_id IS '售后短ID';
COMMENT ON COLUMN erp_document_items.good_item_count IS '良品数量';
COMMENT ON COLUMN erp_document_items.bad_item_count IS '残次品数量';

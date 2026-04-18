-- ============================================================
-- 081: erp_document_items 订单+售后字段扩展（93列）
--
-- 快麦 API 返回 80+ 字段，此前只同步约 35 个。
-- 本迁移补全缺失字段，提升数据完整性。
--
-- 设计文档: docs/document/TECH_ERP数据完整性与查询准确性.md §3
-- 约束: 154 列宽表是受限于现有 delete+insert 同步架构的妥协方案，
--       拆表（订单头+子项）已评估但推迟（需重写同步层+查询层+RPC）。
-- ============================================================

-- 不用事务包裹（ADD COLUMN IF NOT EXISTS 幂等安全）

-- ╔══════════════════════════════════════════════════════════╗
-- ║  前置类型检查（防止列类型冲突）                           ║
-- ╚══════════════════════════════════════════════════════════╝

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
     WHERE table_name='erp_document_items' AND column_name='trade_tags'
     AND data_type != 'jsonb')
  THEN RAISE EXCEPTION 'trade_tags column exists with wrong type';
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
     WHERE table_name='erp_document_items' AND column_name='exception_tags'
     AND udt_name != '_text')
  THEN RAISE EXCEPTION 'exception_tags column exists with wrong type (expected text[])';
  END IF;
END $$;

-- ╔══════════════════════════════════════════════════════════╗
-- ║  订单头级别字段（37列）— 同一 doc_id 所有行共享          ║
-- ╚══════════════════════════════════════════════════════════╝

-- ── 标签/异常/刷单（最关键的3个字段） ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS trade_tags JSONB;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS exception_tags TEXT[];
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_scalping SMALLINT DEFAULT 0;

-- ── 金额/费用 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS total_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS ac_payment NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS actual_post_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS theory_post_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sale_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sale_price NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS packma_cost NUMERIC(12,2);

-- ── 状态/标记 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS unified_status VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS stock_status VARCHAR(16);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_handler_memo SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_handler_message SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_package SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_presell SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS seller_flag SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS belong_type SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS convert_type SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS express_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS deliver_status SMALLINT;

-- ── 时间 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS audit_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS timeout_action_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS deliver_print_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS express_print_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS end_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS pt_consign_time TIMESTAMPTZ;

-- ── 物流/仓储 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS weight NUMERIC(10,3);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS net_weight NUMERIC(10,3);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS volume NUMERIC(10,4);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS template_name VARCHAR(64);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS warehouse_id INTEGER;

-- ── 拆单/合并 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS split_sid VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS split_type SMALLINT;

-- ── 统计字段 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_num INTEGER;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_kind_num INTEGER;

-- ── 其他 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS receiver_street VARCHAR(128);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS trade_invoice JSONB;


-- ╔══════════════════════════════════════════════════════════╗
-- ║  订单子项级别字段（20列）— 每个子项独立                   ║
-- ╚══════════════════════════════════════════════════════════╝

-- ── 优惠/金额 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_discount_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_discount_rate NUMERIC(5,4);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_ac_payment NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_total_fee NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS divide_order_fee NUMERIC(12,2);

-- ── 商品信息 ──
-- sku_properties_name 已存在（073），IF NOT EXISTS 跳过
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sku_properties_name TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sys_title VARCHAR(256);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sys_sku_properties_name TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS pic_path TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sys_pic_path TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS suits JSONB;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS order_ext JSONB;

-- ── 数量/状态 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS gift_num INTEGER DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS stock_num INTEGER;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_net_weight NUMERIC(10,3);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS insufficient_canceled SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_is_cancel SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_is_presell SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_virtual SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS estimate_con_time TIMESTAMPTZ;


-- ╔══════════════════════════════════════════════════════════╗
-- ║  售后头级别字段（22列）                                   ║
-- ╚══════════════════════════════════════════════════════════╝

-- ── 关联/标识 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS order_sid VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS order_type_ref SMALLINT;
-- order_type_ref 冗余原因：售后关联原订单需 JOIN 同表，198万行下性能差。
-- order_type 创建时确定不会变，冗余安全。

-- ── 买家信息 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS buyer_name VARCHAR(128);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS buyer_phone VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS wangwang_num VARCHAR(64);

-- ── 时间 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS apply_date TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS after_sale_app_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS platform_complete_time TIMESTAMPTZ;

-- ── 状态 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS online_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS online_status_text VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS platform_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS handler_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS handler_status_text VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS deal_result SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS advance_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS advance_status_text VARCHAR(32);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS dest_work_order_status SMALLINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS storage_progress SMALLINT;

-- ── 仓库/沟通 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS refund_warehouse_id INTEGER;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS trade_warehouse_name VARCHAR(64);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS message_memos JSONB;


-- ╔══════════════════════════════════════════════════════════╗
-- ║  售后子项级别字段（14列）                                 ║
-- ╚══════════════════════════════════════════════════════════╝

ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_refund_money NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_raw_refund_money NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS refundable_money NUMERIC(12,2);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS properties_name TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_pic_path TEXT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS receive_goods_time TIMESTAMPTZ;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_detail_id BIGINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS item_snapshot_id BIGINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS num_iid BIGINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS sku_id BIGINT;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_gift SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS is_match SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS suite SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS suite_type SMALLINT DEFAULT 0;


-- ╔══════════════════════════════════════════════════════════╗
-- ║  索引                                                     ║
-- ╚══════════════════════════════════════════════════════════╝

-- 刷单过滤核心索引
CREATE INDEX IF NOT EXISTS idx_doc_items_scalping
    ON erp_document_items (is_scalping) WHERE is_scalping = 1;

-- 统一状态
CREATE INDEX IF NOT EXISTS idx_doc_items_unified_status
    ON erp_document_items (unified_status);

-- 注意：trade_tags 的 GIN 索引一期不加（一期只写不查）。
-- 二期开放标签查询时再加：
-- CREATE INDEX IF NOT EXISTS idx_doc_items_trade_tags
--     ON erp_document_items USING GIN (trade_tags);


-- ╔══════════════════════════════════════════════════════════╗
-- ║  归档表同步加列（与热表保持一致）                         ║
-- ╚══════════════════════════════════════════════════════════╝

-- ── 订单头级别（37列） ──
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS trade_tags JSONB;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS exception_tags TEXT[];
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_scalping SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS total_fee NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS ac_payment NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS actual_post_fee NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS theory_post_fee NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS sale_fee NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS sale_price NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS packma_cost NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS unified_status VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS stock_status VARCHAR(16);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_handler_memo SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_handler_message SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_package SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_presell SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS seller_flag SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS belong_type SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS convert_type SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS express_status SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS deliver_status SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS audit_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS timeout_action_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS deliver_print_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS express_print_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS end_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS pt_consign_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS weight NUMERIC(10,3);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS net_weight NUMERIC(10,3);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS volume NUMERIC(10,4);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS template_name VARCHAR(64);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS warehouse_id INTEGER;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS split_sid VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS split_type SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_num INTEGER;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_kind_num INTEGER;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS receiver_street VARCHAR(128);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS trade_invoice JSONB;

-- ── 订单子项级别（20列） ──
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_discount_fee NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_discount_rate NUMERIC(5,4);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_ac_payment NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_total_fee NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS divide_order_fee NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS sku_properties_name TEXT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS sys_title VARCHAR(256);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS sys_sku_properties_name TEXT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS pic_path TEXT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS sys_pic_path TEXT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS suits JSONB;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS order_ext JSONB;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS gift_num INTEGER DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS stock_num INTEGER;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_net_weight NUMERIC(10,3);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS insufficient_canceled SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_is_cancel SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_is_presell SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_virtual SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS estimate_con_time TIMESTAMPTZ;

-- ── 售后头级别（22列） ──
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS order_sid VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS order_type_ref SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS buyer_name VARCHAR(128);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS buyer_phone VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS wangwang_num VARCHAR(64);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS apply_date TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS after_sale_app_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS platform_complete_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS online_status SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS online_status_text VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS platform_status SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS handler_status SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS handler_status_text VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS deal_result SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS advance_status SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS advance_status_text VARCHAR(32);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS dest_work_order_status SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS storage_progress SMALLINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS refund_warehouse_id INTEGER;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS trade_warehouse_name VARCHAR(64);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS message_memos JSONB;

-- ── 售后子项级别（14列） ──
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_refund_money NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_raw_refund_money NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS refundable_money NUMERIC(12,2);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS properties_name TEXT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_pic_path TEXT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS receive_goods_time TIMESTAMPTZ;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_detail_id BIGINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS item_snapshot_id BIGINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS num_iid BIGINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS sku_id BIGINT;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_gift SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS is_match SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS suite SMALLINT DEFAULT 0;
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS suite_type SMALLINT DEFAULT 0;

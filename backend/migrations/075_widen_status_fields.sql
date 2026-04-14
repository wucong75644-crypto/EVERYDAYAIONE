-- 075: 状态字段加宽 VARCHAR(32) → VARCHAR(64)
-- 原因：快麦 ERP refundStatus 值 "WAIT_BUYER_CONFIRM_REDO_SEND_GOODS"（39字符）
-- 超过 VARCHAR(32) 限制，导致订单写入失败。
-- 同时预防 doc_status / order_status / status_name 等同类字段未来溢出。

-- ── erp_document_items ──────────────────────────────────

ALTER TABLE erp_document_items
    ALTER COLUMN doc_status    TYPE VARCHAR(64),
    ALTER COLUMN order_status  TYPE VARCHAR(64),
    ALTER COLUMN refund_status TYPE VARCHAR(64),
    ALTER COLUMN status_name   TYPE VARCHAR(64),
    ALTER COLUMN good_status   TYPE VARCHAR(64);

-- ── erp_document_items_archive（同步） ──────────────────

ALTER TABLE erp_document_items_archive
    ALTER COLUMN doc_status    TYPE VARCHAR(64),
    ALTER COLUMN order_status  TYPE VARCHAR(64),
    ALTER COLUMN refund_status TYPE VARCHAR(64),
    ALTER COLUMN status_name   TYPE VARCHAR(64),
    ALTER COLUMN good_status   TYPE VARCHAR(64);

-- 074: 收件人字段加宽 — 适配平台隐私加密密文
-- 原因：快麦 ERP 对抖音/快手等平台的收件人信息做了加密（##/$$前缀的 Base64），
-- 密文长度 200-450 字符，远超原 VARCHAR(32/64) 限制。
-- 加密前缀：## = 姓名，$$ = 手机，~ = 地址

-- ── erp_document_items ──────────────────────────────────

ALTER TABLE erp_document_items
    ALTER COLUMN receiver_name     TYPE VARCHAR(512),
    ALTER COLUMN receiver_mobile   TYPE VARCHAR(512),
    ALTER COLUMN receiver_phone    TYPE VARCHAR(512),
    ALTER COLUMN receiver_state    TYPE VARCHAR(512),
    ALTER COLUMN receiver_city     TYPE VARCHAR(512),
    ALTER COLUMN receiver_district TYPE VARCHAR(512),
    ALTER COLUMN receiver_address  TYPE VARCHAR(1024),
    ALTER COLUMN buyer_nick        TYPE VARCHAR(512);

-- ── erp_document_items_archive（同步） ──────────────────

ALTER TABLE erp_document_items_archive
    ALTER COLUMN receiver_name     TYPE VARCHAR(512),
    ALTER COLUMN receiver_mobile   TYPE VARCHAR(512),
    ALTER COLUMN receiver_phone    TYPE VARCHAR(512),
    ALTER COLUMN receiver_state    TYPE VARCHAR(512),
    ALTER COLUMN receiver_city     TYPE VARCHAR(512),
    ALTER COLUMN receiver_district TYPE VARCHAR(512),
    ALTER COLUMN receiver_address  TYPE VARCHAR(1024),
    ALTER COLUMN buyer_nick        TYPE VARCHAR(512);

-- ============================================================
-- 082: 订单分类规则表（多租户可配置）
--
-- 支持"排除优先 + 有效兜底"的互斥分类模型。
-- 一期规则加载只查全局（shop_id IS NULL），二期做店铺级覆盖。
--
-- 设计文档: docs/document/TECH_ERP数据完整性与查询准确性.md §3.5
-- ============================================================

CREATE TABLE IF NOT EXISTS erp_classification_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    shop_id UUID DEFAULT NULL,          -- 预留：NULL=全局规则，非NULL=店铺级覆盖（二期）
    doc_type VARCHAR(32) NOT NULL DEFAULT 'order',
    rule_name VARCHAR(64) NOT NULL,
    rule_icon VARCHAR(8) DEFAULT '🔸',
    priority SMALLINT DEFAULT 0,        -- 数字小=优先匹配
    conditions JSONB NOT NULL,          -- 条件列表，内部 AND；空数组=永远匹配（兜底）
    is_valid_order BOOLEAN DEFAULT FALSE,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_class_rules_org_doc
    ON erp_classification_rules (org_id, doc_type, enabled);

COMMENT ON TABLE erp_classification_rules IS
    '订单分类规则表（多租户），排除优先+有效兜底，一期仅全局规则（shop_id IS NULL）';

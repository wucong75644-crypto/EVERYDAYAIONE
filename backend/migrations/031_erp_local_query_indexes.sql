-- 031: ERP 本地优先查询补充索引
-- 技术设计文档: docs/document/TECH_ERP本地优先统一查询架构.md §5
-- 释放 erp_document_items 表中已存储但未建索引的查询维度

-- 按快递单号查订单（local_doc_query express_no 维度）
CREATE INDEX IF NOT EXISTS idx_doc_items_express
  ON erp_document_items (express_no)
  WHERE express_no IS NOT NULL;

-- 按采购/收货/上架/采退单号查单据（local_doc_query doc_code 维度）
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_code
  ON erp_document_items (doc_code, doc_type)
  WHERE doc_code IS NOT NULL;

-- 按供应商查采购/收货/采退（API 不支持此维度，本地独有能力）
CREATE INDEX IF NOT EXISTS idx_doc_items_supplier
  ON erp_document_items (supplier_name, doc_type)
  WHERE supplier_name IS NOT NULL;

-- 冷表同步补充（归档查询也需要这些索引）
CREATE INDEX IF NOT EXISTS idx_archive_items_express
  ON erp_document_items_archive (express_no)
  WHERE express_no IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_archive_items_doc_code
  ON erp_document_items_archive (doc_code, doc_type)
  WHERE doc_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_archive_items_supplier
  ON erp_document_items_archive (supplier_name, doc_type)
  WHERE supplier_name IS NOT NULL;

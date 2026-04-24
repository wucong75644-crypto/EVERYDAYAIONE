-- 095: 售后工单回填 warehouse_name 和 doc_code
--
-- 根因：快麦售后 API 返回了 tradeWarehouseName / refundWarehouseName / shortId，
--       同步代码存到了售后专属字段（refund_warehouse_name / extra_json.shortId），
--       但通用字段 warehouse_name 和 doc_code 未写入。
--       导致：53万条售后记录按仓库统计时丢失、单据编号为空。

-- 1. warehouse_name：优先 trade_warehouse_name（发货仓），refund_warehouse_name 兜底
UPDATE erp_document_items
SET warehouse_name = COALESCE(trade_warehouse_name, refund_warehouse_name)
WHERE doc_type = 'aftersale'
  AND warehouse_name IS NULL
  AND (trade_warehouse_name IS NOT NULL OR refund_warehouse_name IS NOT NULL);

-- 2. doc_code：从 extra_json.shortId 提取
UPDATE erp_document_items
SET doc_code = extra_json->>'shortId'
WHERE doc_type = 'aftersale'
  AND doc_code IS NULL
  AND extra_json->>'shortId' IS NOT NULL;

-- 归档表同样回填
UPDATE erp_document_items_archive
SET warehouse_name = COALESCE(trade_warehouse_name, refund_warehouse_name)
WHERE doc_type = 'aftersale'
  AND warehouse_name IS NULL
  AND (trade_warehouse_name IS NOT NULL OR refund_warehouse_name IS NOT NULL);

UPDATE erp_document_items_archive
SET doc_code = extra_json->>'shortId'
WHERE doc_type = 'aftersale'
  AND doc_code IS NULL
  AND extra_json->>'shortId' IS NOT NULL;

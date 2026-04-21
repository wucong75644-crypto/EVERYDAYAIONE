-- 090: 采购/收货/采退金额从"分"修正为"元"
--
-- 根因：快麦 API 不同模块金额单位不一致：
--   - 订单/售后：返回"元"（如 price=23.90）
--   - 采购/收货/采退：返回"分"（如 price=2600 实际 ¥26.00）
--   同步代码未做转换，导致采购类金额放大 100 倍。
--
-- 修复范围：
--   1. erp_document_items 主表：price、amount 列 ÷ 100
--   2. erp_document_items_archive 归档表：同上
--   3. extra_json 中的金额字段 ÷ 100
--
-- 同步代码已在 erp_sync_handlers.py 修复（_fen_to_yuan），
-- 本迁移修正历史存量数据。

BEGIN;

-- ── Part 1: 主表 price/amount ──────────────────────────

UPDATE erp_document_items
SET price = price / 100,
    amount = amount / 100
WHERE doc_type IN ('purchase', 'receipt', 'purchase_return')
  AND (price IS NOT NULL OR amount IS NOT NULL);

-- ── Part 2: 归档表 price/amount ────────────────────────

UPDATE erp_document_items_archive
SET price = price / 100,
    amount = amount / 100
WHERE doc_type IN ('purchase', 'receipt', 'purchase_return')
  AND (price IS NOT NULL OR amount IS NOT NULL);

-- ── Part 3: extra_json 金额字段 ────────────────────────

-- purchase: totalAmount, actualTotalAmount, totalFee, amendAmount
UPDATE erp_document_items
SET extra_json = extra_json
    || CASE WHEN extra_json ? 'totalAmount'
            THEN jsonb_build_object('totalAmount', (extra_json->>'totalAmount')::numeric / 100)
            ELSE '{}'::jsonb END
    || CASE WHEN extra_json ? 'actualTotalAmount'
            THEN jsonb_build_object('actualTotalAmount', (extra_json->>'actualTotalAmount')::numeric / 100)
            ELSE '{}'::jsonb END
    || CASE WHEN extra_json ? 'totalFee'
            THEN jsonb_build_object('totalFee', (extra_json->>'totalFee')::numeric / 100)
            ELSE '{}'::jsonb END
    || CASE WHEN extra_json ? 'amendAmount'
            THEN jsonb_build_object('amendAmount', (extra_json->>'amendAmount')::numeric / 100)
            ELSE '{}'::jsonb END
WHERE doc_type = 'purchase'
  AND extra_json IS NOT NULL
  AND (extra_json ? 'totalAmount' OR extra_json ? 'actualTotalAmount'
       OR extra_json ? 'totalFee' OR extra_json ? 'amendAmount');

-- receipt: totalDetailFee
UPDATE erp_document_items
SET extra_json = extra_json
    || jsonb_build_object('totalDetailFee', (extra_json->>'totalDetailFee')::numeric / 100)
WHERE doc_type = 'receipt'
  AND extra_json IS NOT NULL
  AND extra_json ? 'totalDetailFee';

-- purchase_return: totalAmount
UPDATE erp_document_items
SET extra_json = extra_json
    || jsonb_build_object('totalAmount', (extra_json->>'totalAmount')::numeric / 100)
WHERE doc_type = 'purchase_return'
  AND extra_json IS NOT NULL
  AND extra_json ? 'totalAmount';

-- 归档表同理（extra_json）
UPDATE erp_document_items_archive
SET extra_json = extra_json
    || CASE WHEN extra_json ? 'totalAmount'
            THEN jsonb_build_object('totalAmount', (extra_json->>'totalAmount')::numeric / 100)
            ELSE '{}'::jsonb END
    || CASE WHEN extra_json ? 'actualTotalAmount'
            THEN jsonb_build_object('actualTotalAmount', (extra_json->>'actualTotalAmount')::numeric / 100)
            ELSE '{}'::jsonb END
    || CASE WHEN extra_json ? 'totalFee'
            THEN jsonb_build_object('totalFee', (extra_json->>'totalFee')::numeric / 100)
            ELSE '{}'::jsonb END
    || CASE WHEN extra_json ? 'amendAmount'
            THEN jsonb_build_object('amendAmount', (extra_json->>'amendAmount')::numeric / 100)
            ELSE '{}'::jsonb END
WHERE doc_type IN ('purchase', 'purchase_return')
  AND extra_json IS NOT NULL
  AND (extra_json ? 'totalAmount' OR extra_json ? 'actualTotalAmount'
       OR extra_json ? 'totalFee' OR extra_json ? 'amendAmount');

UPDATE erp_document_items_archive
SET extra_json = extra_json
    || jsonb_build_object('totalDetailFee', (extra_json->>'totalDetailFee')::numeric / 100)
WHERE doc_type = 'receipt'
  AND extra_json IS NOT NULL
  AND extra_json ? 'totalDetailFee';

COMMIT;

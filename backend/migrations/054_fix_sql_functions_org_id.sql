-- 054: SQL 函数加 org_id 支持
-- 多租户隔离架构 P3 — 技术方案 §6.4
--
-- 改造函数：
-- 1. increment_message_count — 加 p_org_id，UPDATE 时校验 conversation 归属
-- 2. erp_aggregate_daily_stats_batch — 加 p_org_id，按企业过滤+传递
--
-- 3. erp_aggregate_daily_stats — 改为 DELETE+INSERT（COALESCE 索引与 ON CONFLICT 不兼容）
--
-- 注意：erp_try_acquire_sync_lock 已在 039 迁移中修复。
-- cleanup_expired_credit_locks 内部调用 atomic_refund_credits 自带 org_id 继承，无需改造。

-- ============================================================
-- 1. increment_message_count — 加 p_org_id 参数
-- ============================================================
-- 旧签名：increment_message_count(conv_id UUID)
-- 新签名：increment_message_count(conv_id UUID, p_org_id UUID DEFAULT NULL)
-- 变更：UPDATE 条件追加 org_id 校验（防止跨企业操作）

CREATE OR REPLACE FUNCTION increment_message_count(
    conv_id UUID,
    p_org_id UUID DEFAULT NULL
)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    UPDATE conversations
    SET message_count = message_count + 1, updated_at = NOW()
    WHERE id = conv_id
      AND (
          (p_org_id IS NULL AND org_id IS NULL)
          OR org_id = p_org_id
      );
END;
$$;


-- ============================================================
-- 2. erp_aggregate_daily_stats — DELETE+INSERT 替代 ON CONFLICT
-- ============================================================
-- 旧方式：INSERT ... ON CONFLICT (stat_date, outer_id, COALESCE(sku_outer_id, ''))
-- 问题：052 索引加了 org_id 后，ON CONFLICT 列数不匹配（3列≠4列），会报错
-- 新方式：先 DELETE 旧记录再 INSERT（同在一个事务中，安全）

CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats(
    p_outer_id VARCHAR, p_stat_date DATE, p_org_id UUID DEFAULT NULL
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM erp_product_daily_stats
    WHERE stat_date = p_stat_date AND outer_id = p_outer_id
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id);

    INSERT INTO erp_product_daily_stats (
        stat_date, outer_id, sku_outer_id, item_name, org_id,
        purchase_count, purchase_qty, purchase_received_qty, purchase_amount,
        receipt_count, receipt_qty, shelf_count, shelf_qty,
        purchase_return_count, purchase_return_qty, purchase_return_amount,
        aftersale_count, aftersale_refund_count, aftersale_return_count,
        aftersale_exchange_count, aftersale_reissue_count,
        aftersale_reject_count, aftersale_repair_count, aftersale_other_count,
        aftersale_qty, aftersale_amount,
        order_count, order_qty, order_amount,
        order_shipped_count, order_finished_count,
        order_refund_count, order_cancelled_count, order_cost, updated_at
    )
    SELECT
        p_stat_date, p_outer_id, NULL, MAX(item_name), p_org_id,
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(quantity_received) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'receipt'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'receipt'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'shelf'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'shelf'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase_return'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase_return'), 0),
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
      AND doc_created_at >= p_stat_date
      AND doc_created_at < p_stat_date + INTERVAL '1 day'
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id);
END;
$$;


-- ============================================================
-- 3. erp_aggregate_daily_stats_batch — 加 p_org_id 参数
-- ============================================================
-- 旧签名：erp_aggregate_daily_stats_batch(p_since_date DATE)
-- 新签名：erp_aggregate_daily_stats_batch(p_since_date DATE, p_org_id UUID DEFAULT NULL)
-- 变更：
--   - SELECT 追加 org_id 过滤
--   - 调用 erp_aggregate_daily_stats 时传入 p_org_id

CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats_batch(
    p_since_date DATE,
    p_org_id UUID DEFAULT NULL
)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE v_count INTEGER := 0; v_rec RECORD;
BEGIN
    FOR v_rec IN
        SELECT DISTINCT outer_id, (doc_created_at::DATE)::TEXT AS stat_date
        FROM erp_document_items
        WHERE doc_created_at >= p_since_date
          AND outer_id IS NOT NULL
          AND (
              (p_org_id IS NULL AND org_id IS NULL)
              OR org_id = p_org_id
          )
    LOOP
        PERFORM erp_aggregate_daily_stats(v_rec.outer_id, v_rec.stat_date::DATE, p_org_id);
        v_count := v_count + 1;
    END LOOP;
    RETURN v_count;
END;
$$;

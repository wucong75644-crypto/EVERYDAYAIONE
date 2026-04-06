-- ============================================================
-- 045: 店铺列表去重 RPC
--
-- local_shop_list 工具需要从 erp_document_items 中提取
-- 所有出过单的店铺（DISTINCT shop_name + platform）。
-- DB 端去重避免拉大量数据到应用层。
-- ============================================================

CREATE OR REPLACE FUNCTION erp_distinct_shops(
    p_org_id UUID DEFAULT NULL,
    p_platform VARCHAR DEFAULT NULL
) RETURNS TABLE(shop_name VARCHAR, platform VARCHAR)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT d.shop_name, d.platform
    FROM erp_document_items d
    WHERE d.doc_type = 'order'
      AND d.shop_name IS NOT NULL
      AND d.shop_name != ''
      AND (
          (p_org_id IS NULL AND d.org_id IS NULL)
          OR d.org_id = p_org_id
      )
      AND (p_platform IS NULL OR d.platform = p_platform)
    ORDER BY d.platform, d.shop_name;
$$;

COMMENT ON FUNCTION erp_distinct_shops IS '店铺列表去重查询（local_shop_list 工具专用）';

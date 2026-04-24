-- 096: 上架单补全 supplier_name / creator_name / purchase_order_code / price
--
-- 根因：快麦上架单 API 返回了 supplierName / creator / weCode / item.price，
--       但同步代码只映射了基础字段，这四个未写入。
--       导致：7872 条上架记录这些字段全空。
--
-- 同步代码已修复（erp_sync_handlers.py sync_shelf），新数据会自动写入。
-- 历史数据需触发一次上架单重同步来补全（这些字段未存入 extra_json，无法从本地回填）。
--
-- 重同步命令（部署后在生产执行）：
--   curl -X POST http://localhost:8000/api/erp/sync/shelf?force=true

SELECT 1; -- 无本地数据变更，仅同步代码修复

# 维度 ID 补全与星型模型改造

> 版本: v1.0 | 日期: 2026-04-24

## 1. 背景

`erp_document_items` 事实表中 `shop_name`、`supplier_name`、`warehouse_name` 等字段直接存了名称字符串，没有存维度 ID。这违反了星型模型的设计原则，导致：

1. **数据一致性**：维度改名后历史记录还是老名字
2. **JOIN 不可靠**：店铺同名（"默认店铺"8条、"蓝恩集美优品"4条）无法准确匹配
3. **字段缺失**：采购类 API 不返回 `title`，`item_name` 全空（已修复但用的是 workaround）

## 2. 关键发现

- **shop_id ≠ userId**：`erp_shops.shop_id='900187178'`，但快麦 API 返回的是 `userId='900187683'`。二者是不同字段
- **warehouse_id 类型不一致**：事实表 INTEGER，维度表 VARCHAR(64)
- **supplier/warehouse 无同名**：可安全用名称反查
- **店铺同名严重**：必须用 `name + platform` 双条件匹配

## 3. 维度表关联方式

| 维度 | 事实表列 | 维度表 | JOIN 键 |
|------|---------|--------|---------|
| 商品 | `outer_id` | `erp_products.outer_id` | 直接 JOIN |
| 店铺 | `shop_user_id`（新增） | `erp_shops.user_id`（新增） | JOIN on user_id + org_id |
| 仓库 | `warehouse_id`（已有，类型修正） | `erp_warehouses.warehouse_id` | JOIN on warehouse_id + org_id |
| 供应商 | `supplier_code`（新增） | `erp_suppliers.code` | JOIN on code + org_id |

## 4. API 字段验证（全部经过实际 API 调用验证）

| 单据 | warehouse_id | supplier_code | shop_user_id |
|------|:----------:|:------------:|:----------:|
| order | doc.warehouseId ✅ | — | doc.userId ✅ |
| purchase | doc.receiveWarehouseId ✅ | doc/item.supplierCode ✅ | — |
| receipt | doc.warehouseId ✅ | doc/item.supplierCode ✅ | — |
| shelf | doc.warehouseId ✅ | 无code，需反查 supplierName | — |
| purchase_return | doc.warehouseId ✅ | doc/item.supplierCode ✅ | — |
| aftersale | doc.tradeWarehouseId ✅ | — | doc.userId ✅ |

## 5. 实施方案

### 迁移文件

| 迁移 | Phase | 内容 |
|------|-------|------|
| 097 | 0 | warehouse_id INTEGER→VARCHAR(64) + erp_shops 加 user_id 列 |
| 098 | 1 | 事实表加 supplier_code + shop_user_id 列 |
| 099 | 2 | 历史数据回填（名称反查维度表） |
| 100 | 3 | RPC 改造：ID 分组 + LEFT JOIN 取名称 |

### 代码文件

| 文件 | 改动 |
|------|------|
| `erp_sync_handlers.py` | 4处采购类加 warehouse_id + supplier_code |
| `erp_sync_row_builders.py` | order/aftersale 加 shop_user_id, warehouse_id str()包裹 |
| `erp_sync_config_handlers.py` | shop 同步写入 user_id |
| `erp_sync_service.py` | 新增 resolve_supplier_code() 缓存方法 |
| `erp_unified_schema.py` | COLUMN_WHITELIST 新增3个ID字段 |
| `erp_local_tools.py` | 查询参数文档新增ID字段 |

## 6. 部署顺序

```
097(类型对齐) → 098(加列) → 099(回填) → 100(RPC改造)
```

每个迁移独立可部署。部署后验证：
```sql
SELECT doc_type, COUNT(*) total,
  COUNT(warehouse_id) has_wh, COUNT(supplier_code) has_sup, COUNT(shop_user_id) has_shop
FROM erp_document_items GROUP BY doc_type;
```

## 7. 向后兼容

- 名称字段（shop_name / supplier_name / warehouse_name）暂时保留，双写
- RPC 的 `group_key` 输出值仍是名称（通过 JOIN），Python 端无需改动
- ID 找不到时 COALESCE 降级显示名称或 ID 值
- 灰度验证稳定后再考虑删除冗余名称字段

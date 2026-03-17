# Phase 5 — 三批次执行方案

---

## 5A: 直接宽泛查询 + fetch_all翻页

### 问题
当前「精确查询→零结果→宽泛查询」浪费一次API调用，且宽泛查询只查1页数据不全。

### 方案
编码参数直接用基础编码宽泛查 + fetch_all翻页拉全量 + 本地匹配。

### 文件清单

| 文件 | 操作 |
|------|------|
| `backend/services/kuaimai/param_guardrails.py` | 删2旧函数 + 新增2函数 |
| `backend/services/kuaimai/dispatcher.py` | 步骤4重构 + fetch_all加max_pages |
| `backend/tests/test_param_guardrails.py` | 删旧测试 + 新增2测试类 |

### 详细改动

#### param_guardrails.py

**删除**（~80行）：`broadened_code_query()` + `_try_broadened_query()`

**新增① `apply_code_broadening()`**（~35行）— API调用**前**介入：
```python
def apply_code_broadening(entry, user_params, api_params) -> Optional[Tuple[str, str]]:
    # 安全门①：写操作跳过
    if entry.is_write:
        return None
    # 安全门②：详情接口跳过（response_key=None）
    if entry.response_key is None:
        return None
    # 安全门③：只认 outer_id/sku_outer_id 单数形式
    code_info = _find_code_param(user_params)
    if not code_info:
        return None
    param_name, original_code = code_info
    base_code = extract_base_code(original_code)
    if not base_code:
        return None

    outer_key = entry.param_map.get("outer_id")
    sku_key = entry.param_map.get("sku_outer_id")
    target_key = outer_key or sku_key
    if not target_key:
        return None

    for k in ("mainOuterId", "skuOuterId", "outerId"):
        api_params.pop(k, None)
    api_params[target_key] = base_code
    api_params["pageSize"] = 100
    return original_code, base_code
```

仅触发4个查询（三重安全门）：

| 安全门 | 排除 |
|-------|------|
| ① is_write | supplier_add/delete, product_add_update_simple, update_history_price, fast_stock_update |
| ② response_key is None | product_detail, sku_info |
| ③ _find_code_param | 订单(order_id), 采购(code), 仓库单据(code), 批量(outer_ids复数), 售后 |

最终触发：stock_status / warehouse_stock / stock_in_out / sku_list

**新增② `match_broadened_results()`**（~25行）— API调用**后**匹配：
```python
def match_broadened_results(entry, data, original_code, base_code) -> Tuple[dict, str]:
    response_key = entry.response_key or "list"
    items = data.get(response_key) or []
    if not items:
        return data, f"⚙ 编码智能匹配: 用基础编码「{base_code}」查询无数据"

    matched = _match_items(items, original_code)
    result_data = dict(data)
    if matched:
        result_data[response_key] = matched
        result_data["total"] = len(matched)
        note = f"⚙ 编码智能匹配: 「{base_code}」查到{len(items)}条，匹配「{original_code}」得{len(matched)}条"
    else:
        note = f"⚙ 编码智能匹配: 「{base_code}」查到{len(items)}条，未匹配「{original_code}」，返回全部"
    return result_data, note
```

**保留不变**：extract_base_code / _find_code_param / _match_items / _is_empty_result / diagnose_empty_result / preprocess_params

#### dispatcher.py

导入改为：`apply_code_broadening, match_broadened_results`（替代 `broadened_code_query`）

步骤4重构：
```python
# 4.1 编码宽泛化（API调用前）
broadening = apply_code_broadening(entry, params, api_params)

try:
    if entry.fetch_all or broadening:
        data = await self._fetch_all_pages(
            entry, api_params, base_url, system_params,
            max_pages=10 if broadening else 0,
        )
    else:
        data = await self._client.request_with_retry(...)
except Exception as e:
    ...

# 4.5 本地匹配（替代旧broadened_code_query）
broadened_note = ""
if broadening:
    original_code, base_code = broadening
    data, broadened_note = match_broadened_results(entry, data, original_code, base_code)
```

`_fetch_all_pages` 加 `max_pages` 参数（+5行）：
```python
async def _fetch_all_pages(self, ..., *, max_pages: int = 0):
    ...
    if max_pages and page >= max_pages:
        logger.warning(f"fetch_all hit max_pages={max_pages}")
        break
```

#### tests/test_param_guardrails.py

删除 `TestBroadenedCodeQuery`（8个旧测试）

新增 `TestApplyCodeBroadening`（6个）：
- 正常宽泛化（sku_outer_id→base code）
- 写操作跳过
- 详情接口(response_key=None)跳过
- 无编码参数跳过
- 纯数字编码跳过
- 只有sku_outer_id映射时的兜底

新增 `TestMatchBroadenedResults`（4个）：
- 精确匹配命中
- 无匹配返回全部
- 空数据
- response_key缺省用"list"

### 验证
```bash
cd /Users/wucong/EVERYDAYAIONE/backend && source venv/bin/activate
python -m pytest tests/test_param_guardrails.py tests/test_param_mapper.py -v --tb=short
python -m pytest tests/ -q --tb=short
```

### 边界情况

| 场景 | 编码 | 基础编码 | 行为 |
|------|------|---------|------|
| SKU查出入库 | SEVENTEENLSG01-01 | SEVENTEENLSG | outerId=SEVENTEENLSG + fetch_all + 匹配 |
| SKU查库存 | DBTXL01-02 | DBTXL | mainOuterId=DBTXL + fetch_all + 匹配 |
| 纯数字 | 12345 | None | 正常精确查询 |
| 纯字母 | DBTXL | None | 正常精确查询 |
| 写操作 | 任意 | - | 不干预 |
| 超10页 | - | - | max_pages=10截断（100×10=1000条） |

---

## 5B: Formatter 全量重构（标签映射表模式）

### 问题
41个formatter硬编码`.get()`挑选字段，遗漏purchaseNum等关键字段。AI看不到未显示的字段，误判为无数据。未来API新增字段也会被遗漏。

### 方案：标签映射表 + 未知字段兜底

#### 核心工具函数（`common.py` 新增）

```python
# 全局跳过字段（图片/系统ID等无业务价值的）
_GLOBAL_SKIP = {
    "picPath", "skuPicPath", "itemPicPath",
    "sysItemId", "sysSkuId",
    "body", "forbiddenField", "solution", "subCode", "subMsg",
    "code", "msg", "traceId",  # 网关级字段
}

def format_item_with_labels(
    item: Dict[str, Any],
    labels: Dict[str, str],
    skip: Set[str] | None = None,
    transforms: Dict[str, Callable] | None = None,
) -> str:
    """通用字段格式化：按标签表展示 + 未知非空字段兜底

    Args:
        item: API返回的单条数据
        labels: {API字段名: 中文标签} 有序映射
        skip: 额外跳过的字段（与_GLOBAL_SKIP合并）
        transforms: {字段名: 转换函数} 如状态码→中文
    """
    all_skip = _GLOBAL_SKIP | (skip or set())
    transforms = transforms or {}
    parts = []

    # 1. 按标签表顺序展示已知字段
    for key, label in labels.items():
        val = item.get(key)
        if val is None or val == "":
            continue
        if key in transforms:
            val = transforms[key](val)
        parts.append(f"{label}: {val}")

    # 2. 未知字段兜底（防止未来API新增字段被遗漏）
    for key, val in item.items():
        if key in labels or key in all_skip:
            continue
        if val is None or val == "" or val == 0:
            continue
        if isinstance(val, (list, dict)):
            continue  # 嵌套数据由各formatter自行处理
        parts.append(f"{key}: {val}")

    return " | ".join(parts)
```

#### 各文件改动

##### product.py（6个formatter）

**`_format_inventory`** — 库存状态（补全purchaseNum等4个遗漏字段）：
```python
_INVENTORY_LABELS = {
    "title": "名称", "mainOuterId": "编码", "outerId": "SKU",
    "propertiesName": "规格",
    "totalAvailableStockSum": "总库存", "sellableNum": "可售",
    "totalLockStock": "锁定", "purchaseNum": "采购在途",
    "allocateNum": "调拨", "totalDefectiveStock": "残次品",
    "refundStock": "退款库存",
    "purchasePrice": "采购价", "sellingPrice": "销售价", "marketPrice": "市场价",
    "stockStatus": "状态", "wareHouseId": "仓库ID",
    "brand": "品牌", "cidName": "分类",
    "unit": "单位", "stockModifiedTime": "库存更新时间",
    "itemBarcode": "条码", "skuBarcode": "SKU条码",
    "supplierCodes": "供应商编码", "supplierNames": "供应商",
}
_INVENTORY_SKIP = {"shortTitle"}  # title已有，shortTitle冗余
_INVENTORY_TRANSFORMS = {
    "stockStatus": lambda v: {0:"正常",1:"警戒",2:"无货",3:"超卖"}.get(v, str(v)),
    "purchasePrice": lambda v: f"¥{v}",
    "sellingPrice": lambda v: f"¥{v}",
    "marketPrice": lambda v: f"¥{v}",
}
```

**`format_warehouse_stock`** — 仓库库存（补全locked/purchaseNum等）：
```python
_WH_STOCK_LABELS = {
    "title": "名称", "outerId": "编码",
    "warehouseName": "仓库", "wareHouseId": "仓库ID",
    "sellableNum": "可售", "totalLockStock": "锁定",
    "purchaseNum": "采购在途", "totalAvailableStockSum": "总库存",
}
```

**`format_stock_in_out`** — 出入库流水（补全仓库/单据号等）：
```python
_STOCK_IO_LABELS = {
    "outerId": "编码", "title": "名称",
    "bizType": "类型", "changeNum": "变动数量",
    "warehouseName": "仓库", "orderNumber": "单据号",
    "created": "时间", "remark": "备注",
}
_STOCK_IO_TRANSFORMS = {"created": format_timestamp}
```

**`_format_product`** — 商品列表（补全价格/分类等）：
```python
_PRODUCT_LABELS = {
    "title": "名称", "outerId": "编码", "barcode": "条码",
    "weight": "重量", "unit": "单位",
    "purchasePrice": "采购价", "sellingPrice": "销售价",
    "brand": "品牌", "isSkuItem": "多规格", "activeStatus": "状态",
}
_PRODUCT_TRANSFORMS = {
    "isSkuItem": lambda v: "是" if v else "否",
    "activeStatus": lambda v: "启用" if v == 1 else "停用",
    "weight": lambda v: f"{v}g" if v else "",
    "purchasePrice": lambda v: f"¥{v}",
}
```

**`_format_product_detail`** — 商品详情：同上标签 + 保留嵌套SKU列表逻辑
**`_format_sku_line`** — SKU行：补全价格/重量字段

##### trade.py（6个formatter）

**`_format_order`** — 订单（补全商品/物流/仓库等）：
```python
_ORDER_LABELS = {
    "tid": "订单号", "sid": "系统单号",
    "sysStatus": "状态", "buyerNick": "买家",
    "payment": "付款金额", "shopName": "店铺",
    "source": "平台", "warehouseName": "仓库",
    "created": "下单时间", "payTime": "付款时间",
    "consignTime": "发货时间",
    "sellerMemo": "卖家备注", "buyerMessage": "买家留言",
    "receiverName": "收件人", "receiverPhone": "收件电话",
    "receiverProvince": "省", "receiverCity": "市",
}
_ORDER_TRANSFORMS = {
    "buyerNick": lambda v: v or "（隐私保护）",
    "created": format_timestamp, "payTime": format_timestamp,
    "consignTime": format_timestamp,
}
```

**`_format_shipment`** — 发货单：补全金额/物流字段 + 保留嵌套orders逻辑
**`format_order_log`** — 操作日志：转为标签表
**`format_express_list`** — 物流：转为标签表
**`format_logistics_company`** — 物流公司：转为标签表

##### basic.py（5个formatter）

各formatter转为标签映射表模式：
- `format_warehouse_list`：补全类型/面积等字段
- `format_shop_list`：补全平台/到期时间等字段
- `format_tag_list`：保留HTML清理逻辑
- `format_customer_list`：补全地址/等级等字段
- `format_distributor_list`：补全联系方式/状态等字段

##### warehouse.py（10个formatter）

10个formatter全部转为标签映射表，补全遗漏字段。
特殊处理：
- `format_allocate_detail` / `format_inventory_sheet_detail`：保留嵌套items逻辑
- `format_inventory_sheet_detail`：保留系统数/实际数/差异数对比逻辑

##### purchase.py（7个formatter）

7个formatter全部转为标签映射表。
- `format_purchase_order_detail`：保留嵌套items逻辑 + 金额格式化
- `format_purchase_strategy`：保留建议采购数量 vs 当前库存对比

##### aftersales.py（6个formatter）

6个formatter全部转为标签映射表。
- `_format_aftersale_item`：补全退款金额/原因/物流等字段
- `format_repair_detail`：保留嵌套items逻辑

##### qimen.py（2个formatter）

2个formatter转为标签映射表。
- 保留 _ORDER_TYPE_MAP（15种）/ _REFUND_TYPE_MAP（5种）/ _REFUND_STATUS_MAP（10种）
- 保留嵌套orders/items逻辑

### 文件清单

| 文件 | 操作 |
|------|------|
| `formatters/common.py` | 新增 `format_item_with_labels()` + `_GLOBAL_SKIP` |
| `formatters/product.py` | 6个formatter重构 |
| `formatters/trade.py` | 6个formatter重构 |
| `formatters/basic.py` | 5个formatter重构 |
| `formatters/warehouse.py` | 10个formatter重构 |
| `formatters/purchase.py` | 7个formatter重构 |
| `formatters/aftersales.py` | 6个formatter重构 |
| `formatters/qimen.py` | 2个formatter重构 |

### 验证
```bash
cd /Users/wucong/EVERYDAYAIONE/backend && source venv/bin/activate
python -m pytest tests/ -q --tb=short  # 全量回归
```

---

## 5C: 路由提示词更新

### 问题
代码层能力升级后，AI不知道新能力，仍用旧策略（精确查询、去查采购单拿在途数据）。

### 文件
`backend/config/erp_tools.py`

### 改动内容

降级策略部分更新：
```
- 编码查询系统会自动提取基础编码宽泛查询+翻页拉取全量数据+本地匹配，无需手动重试
- stock_status 已包含采购在途(purchaseNum)/调拨/残次品等完整库存字段，查库存相关数据用 stock_status 即可
```

stock_status 的 description 补充：
```
查询库存数量和状态（总库存/可售/锁定/采购在途/调拨/残次品，含各仓汇总）
```

### 验证
部署后实测：`SEVENTEENLSG01-01 查库存和出入库`
- 库存应显示采购在途数量
- 出入库应用宽泛查询返回匹配结果
- AI不应再去调purchase_order_list查在途数据

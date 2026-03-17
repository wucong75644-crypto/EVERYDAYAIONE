# Phase 5B: Formatter 全量重构（标签映射表模式）

> **版本**: V2.0 — 交叉比对 `TECH_快麦API文档_完整.md` 后全量修正
> **修正内容**: 22处字段名错误 + 30+高价值字段遗漏 + 3处结构不匹配 + 2处幽灵字段

## 问题

1. **字段遗漏**：41个formatter硬编码`.get()`挑选字段，遗漏purchaseNum等关键字段
2. **字段名错误**：22处字段名与API实际返回不符（如`allocateNo`→实际是`code`），导致显示为空
3. **结构不匹配**：3处formatter的数据读取结构与API响应结构不一致

AI看不到未显示的字段，误判为无数据。

## 数据流现状
```
API 返回全部字段（30+个）
  → client.py 原样传递（不过滤）
  → dispatcher.py 原样传递给 formatter
  → formatter 硬编码 .get() 挑选显示哪些字段 ← 唯一的过滤点
  → AI 只能看到 formatter 输出的文本
```
数据没有丢失，只是 formatter 没显示（或用了错误的字段名取不到）。

## 方案：标签映射表 + 未知字段兜底

### 核心工具函数（`common.py` 新增）

```python
# 全局跳过字段（图片/系统ID等无业务价值的）
# 注意：code 不放在这里，因为很多item用 code 作为单号
_GLOBAL_SKIP = {
    "picPath", "skuPicPath", "itemPicPath",
    "sysItemId", "sysSkuId",
    "body", "forbiddenField", "solution", "subCode", "subMsg",
    "msg", "traceId",  # 网关级字段
    "companyId",  # 内部公司ID，无业务价值
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

**优势**：
- 已知字段有中文标签，可读性好
- 未来API新增字段自动展示（兜底机制）
- 通过SKIP集合过滤图片路径、系统ID等无业务价值字段
- 新增字段 = 在映射表加一行

---

## 各文件改动

### product.py（6个formatter）

**`_format_inventory`** — 库存状态 `stock.api.status.query`：
```python
_INVENTORY_LABELS = {
    "title": "名称", "mainOuterId": "编码",
    "outerId": "SKU编码", "skuOuterId": "规格编码",
    "propertiesName": "规格",
    "totalAvailableStockSum": "总库存", "sellableNum": "可售",
    "totalAvailableStock": "实际可用",  # ← 新增：物理可用数（≠可售数）
    "totalLockStock": "锁定", "purchaseNum": "采购在途",
    "onTheWayNum": "销退在途",  # ← 新增：客户退货在途
    "allocateNum": "调拨", "totalDefectiveStock": "残次品",
    "refundStock": "退款库存",
    "purchaseStock": "入库暂存",  # ← 新增：已到仓未上架
    "virtualStock": "虚拟库存",  # ← 新增：预售虚拟库存
    "purchasePrice": "采购价", "sellingPrice": "销售价", "marketPrice": "市场价",
    "stockStatus": "状态", "wareHouseId": "仓库ID",
    "brand": "品牌", "cidName": "分类",
    "unit": "单位", "place": "产地",
    "stockModifiedTime": "库存更新时间",
    "itemBarcode": "条码", "skuBarcode": "SKU条码",
    "supplierCodes": "供应商编码", "supplierNames": "供应商",
}
_INVENTORY_SKIP = {"shortTitle"}  # title已有，shortTitle冗余
_INVENTORY_TRANSFORMS = {
    "stockStatus": lambda v: {0:"正常",1:"警戒",2:"无货",3:"超卖"}.get(v, str(v)),
    "purchasePrice": lambda v: f"¥{v}",
    "sellingPrice": lambda v: f"¥{v}",
    "marketPrice": lambda v: f"¥{v}",
    "stockModifiedTime": format_timestamp,
}
```

**`format_warehouse_stock`** — 仓库库存 `erp.item.warehouse.list.get`：

> **结构说明**：API返回嵌套结构 `outerId → skus[] → mainWareHousesStock[]`，仓库级字段在最内层。
> 注意：此API **不返回** `purchaseNum`（原方案为幽灵字段）。

```python
# 仓库级字段（mainWareHousesStock[] 内的每条记录）
_WH_STOCK_LABELS = {
    "name": "仓库",  # API字段是 name，不是 warehouseName
    "id": "仓库ID",
    "code": "仓库编码",
    "totalAvailableStockSum": "总库存",
    "sellableNum": "可售",
    "totalAvailableStock": "实际可用",  # ← 新增
    "totalLockStock": "锁定",
    "totalDefectiveStock": "次品",  # ← 新增
    "stockStatus": "库存状态",  # ← 新增（1=正常,2=警戒,3=无货,4=超卖,6=有货）
    "status": "仓库状态",  # ← 新增（0=停用,1=正常,2=禁止发货）
}
_WH_STOCK_TRANSFORMS = {
    "stockStatus": lambda v: {1:"正常",2:"警戒",3:"无货",4:"超卖",6:"有货"}.get(v, str(v)),
    "status": lambda v: {0:"停用",1:"正常",2:"禁止发货"}.get(v, str(v)),
}
```

**`format_stock_in_out`** — 出入库流水 `erp.item.stock.in.out.list`：

> **待验证**：API文档页面无法提取。字段名 `bizType`/`changeNum`/`created` 来自现有代码，需实际API调用确认。

```python
_STOCK_IO_LABELS = {
    "outerId": "编码", "title": "名称",
    "bizType": "类型", "changeNum": "变动数量",
    "warehouseName": "仓库", "orderNumber": "单据号",
    "created": "时间", "remark": "备注",
}
_STOCK_IO_TRANSFORMS = {"created": format_timestamp}
```

**`_format_product`** — 商品列表 `item.list.query`：

> **关键修正**：此API的销售价字段名是 `priceOutput`，不是 `sellingPrice`。

```python
_PRODUCT_LABELS = {
    "title": "名称", "outerId": "编码", "barcode": "条码",
    "type": "商品类型",  # ← 新增（0=普通,1=sku套件,2=纯套件,3=包材）
    "weight": "重量", "unit": "单位",
    "purchasePrice": "采购价",
    "priceOutput": "销售价",  # ← 修正：API实际字段名（非sellingPrice）
    "marketPrice": "市场价",  # ← 新增
    "brand": "品牌",
    "isSkuItem": "多规格", "isVirtual": "虚拟商品",
    "makeGift": "赠品",
    "activeStatus": "状态",
    "remark": "备注",  # ← 新增
}
_PRODUCT_SKIP = {"shortTitle"}
_PRODUCT_TRANSFORMS = {
    "type": lambda v: {0:"普通",1:"SKU套件",2:"纯套件",3:"包材"}.get(v, str(v)),
    "isSkuItem": lambda v: "是" if v else "否",
    "isVirtual": lambda v: "是" if v else "否",
    "makeGift": lambda v: "是" if v else "否",
    "activeStatus": lambda v: "启用" if v == 1 else "停用",
    "weight": lambda v: f"{v}g" if v else "",
    "purchasePrice": lambda v: f"¥{v}",
    "priceOutput": lambda v: f"¥{v}",
    "marketPrice": lambda v: f"¥{v}",
}
```

**`_format_product_detail`** — 商品详情：同上标签 + 保留嵌套SKU列表逻辑

**`_format_sku_line`** — SKU行：
```python
_SKU_LABELS = {
    "skuOuterId": "编码", "propertiesName": "规格",
    "barcode": "条码",
    "weight": "重量",  # ← 新增
    "purchasePrice": "采购价",  # ← 新增
    "priceOutput": "销售价",  # ← 新增
    "marketPrice": "市场价",  # ← 新增
    "unit": "单位",  # ← 新增
    "activeStatus": "状态",
}
_SKU_TRANSFORMS = {
    "activeStatus": lambda v: "启用" if v == 1 else "停用",
    "weight": lambda v: f"{v}g" if v else "",
    "purchasePrice": lambda v: f"¥{v}",
    "priceOutput": lambda v: f"¥{v}",
    "marketPrice": lambda v: f"¥{v}",
}
```

---

### trade.py（6个formatter）

**`_format_order`** — 订单 `erp.trade.list.query`：

> **关键修正**：省份字段名是 `receiverState`，不是 `receiverProvince`。

```python
_ORDER_LABELS = {
    "tid": "订单号", "sid": "系统单号",
    "type": "订单类型",  # ← 新增（0=普通,7=合并,8=拆分,33=分销,99=出库单）
    "sysStatus": "状态", "buyerNick": "买家",
    "payment": "付款金额", "payAmount": "实付金额",  # ← 新增
    "cost": "成本", "grossProfit": "毛利",  # ← 新增
    "postFee": "运费", "discountFee": "折扣",  # ← 新增
    "shopName": "店铺", "source": "平台", "warehouseName": "仓库",
    "outSid": "快递单号", "expressCompanyName": "快递公司",  # ← 新增
    "created": "下单时间", "payTime": "付款时间", "consignTime": "发货时间",
    "sellerMemo": "卖家备注", "buyerMessage": "买家留言", "sysMemo": "系统备注",  # sysMemo新增
    "isRefund": "退款", "isExcep": "异常", "isHalt": "挂起",  # ← 新增
    "isCancel": "取消", "isUrgent": "加急",  # ← 新增
    "receiverName": "收件人",
    "receiverMobile": "手机",  # ← 新增（比座机更常用）
    "receiverPhone": "电话",
    "receiverState": "省",  # ← 修正：API字段名（非receiverProvince）
    "receiverCity": "市",
    "receiverDistrict": "区",  # ← 新增
    "receiverAddress": "地址",  # ← 新增
}
_ORDER_TRANSFORMS = {
    "type": lambda v: {0:"普通",7:"合并",8:"拆分",33:"分销",99:"出库单"}.get(v, str(v)),
    "buyerNick": lambda v: v or "（隐私保护）",
    "created": format_timestamp, "payTime": format_timestamp,
    "consignTime": format_timestamp,
    "isRefund": lambda v: "是" if v == 1 else "",
    "isExcep": lambda v: "是" if v == 1 else "",
    "isHalt": lambda v: "是" if v == 1 else "",
    "isCancel": lambda v: "是" if v == 1 else "",
    "isUrgent": lambda v: "是" if v == 1 else "",
    "payment": lambda v: f"¥{v}" if v else "",
    "payAmount": lambda v: f"¥{v}" if v else "",
    "cost": lambda v: f"¥{v}" if v else "",
    "grossProfit": lambda v: f"¥{v}" if v else "",
    "postFee": lambda v: f"¥{v}" if v else "",
    "discountFee": lambda v: f"¥{v}" if v else "",
}
```

子订单（`orders[]`）标签：
```python
_SUB_ORDER_LABELS = {
    "sysTitle": "商品", "sysOuterId": "编码", "outerSkuId": "SKU编码",
    "skuPropertiesName": "规格",
    "num": "数量", "price": "单价", "payment": "实付",
    "cost": "成本",
    "refundStatus": "退款状态",
}
```

**`_format_shipment`** — 发货单：同 `_ORDER_LABELS` + 保留嵌套 orders/details 逻辑

> **结构说明**：`erp.trade.outstock.simple.query` 嵌套键是 `orders[]`；`erp.wave.logistics.order.query` 嵌套键是 `details[]`。代码需兼容两种。

**`format_order_log`** — 操作日志 `erp.trade.trace.list`：

> **关键修正**：全部4个字段名与API不符。

```python
_ORDER_LOG_LABELS = {
    "sid": "系统单号",  # ← 新增（多订单日志时区分来源）
    "operateTime": "时间",  # ← 修正（非operTime）
    "action": "操作",  # 保留，API确实返回 action
    "operator": "操作人",  # ← 修正（非operName）
    "content": "内容",  # ← 修正（非remark，API字段名是content）
}
_ORDER_LOG_TRANSFORMS = {"operateTime": format_timestamp}
```

**`format_express_list`** — 多快递单号 `erp.trade.multi.packs.query`：

> **结构修正**：API返回扁平结构 `{cpCode, outSids[], expressName}`，不是列表。需要重写读取逻辑。

```python
# 此API不是列表结构，需要特殊处理
# 响应示例: {"cpCode": "YUNDA", "outSids": ["YT123", "YT456"], "expressName": "韵达快递"}
def format_express_list(data, entry):
    cp_code = data.get("cpCode") or ""
    out_sids = data.get("outSids") or []
    express_name = data.get("expressName") or ""
    if not out_sids:
        return "未找到快递信息"
    lines = [f"快递公司: {express_name} ({cp_code})"]
    for sid in out_sids:
        lines.append(f"  - 单号: {sid}")
    return "\n".join(lines)
```

**`format_logistics_company`** — 物流公司 `erp.trade.logistics.company.user.list`：

> **关键修正**：编码字段是 `cpCode`（非 `code`/`companyCode`）

```python
_LOGISTICS_COMPANY_LABELS = {
    "name": "公司名称",
    "cpCode": "快递编码",  # ← 修正（非code/companyCode）
    "cpType": "服务类型",  # ← 新增（1=直营,2=加盟,3=落地配,4=直营+网点）
    "liveStatus": "状态",  # ← 新增
    "id": "ID",  # ← 新增（下游API需要logisticsCompanyId）
}
_LOGISTICS_COMPANY_TRANSFORMS = {
    "cpType": lambda v: {1:"直营",2:"加盟",3:"落地配",4:"直营+网点"}.get(int(v), str(v)),
    "liveStatus": lambda v: "启用" if v == 1 else "停用",
}
```

---

### basic.py（5个formatter）

**`format_warehouse_list`** — 仓库列表 `erp.warehouse.list.query`：
```python
_WAREHOUSE_LABELS = {
    "name": "名称", "code": "编码",
    "type": "类型",  # ← 新增（0=自有,1=第三方,2=门店）
    "status": "状态",
    "contact": "联系人", "contactPhone": "电话",
    "state": "省", "city": "市", "district": "区",  # ← 新增
    "address": "地址",
    "externalCode": "外部编码",  # ← 新增（三方WMS对接用）
}
_WAREHOUSE_TRANSFORMS = {
    "type": lambda v: {0:"自有",1:"第三方",2:"门店"}.get(v, str(v)),
    "status": lambda v: {0:"停用",1:"正常",2:"禁止发货"}.get(v, str(v)),
}
```

**`format_shop_list`** — 店铺列表 `erp.shop.list.query`：
```python
_SHOP_LABELS = {
    "title": "名称", "shortTitle": "简称",
    "userId": "店铺编码", "shopId": "店铺ID",  # ← 新增
    "source": "平台", "nick": "昵称",
    "state": "状态",  # ← 修正：用4态state替代2态active
    "deadline": "到期时间",  # ← 新增
    "groupName": "店铺组",  # ← 新增
}
_SHOP_TRANSFORMS = {
    "state": lambda v: {1:"停用",2:"未初始化",3:"启用",4:"会话失效"}.get(v, str(v)),
    "deadline": format_timestamp,
}
```

**`format_tag_list`** — 标签列表 `erp.trade.query.tag.list` / `erp.item.tag.list`：
```python
_TAG_LABELS = {
    "tagName": "标签名", "id": "ID",
    "type": "类型",  # ← 新增（0=普通,1=自定义异常,3=系统,-1=系统异常）
    "remark": "说明",
}
_TAG_TRANSFORMS = {
    "type": lambda v: {0:"普通",1:"自定义异常",3:"系统",-1:"系统异常"}.get(v, str(v)),
}
# 保留 remark 的 HTML 清理逻辑（<br/> → 空格）
```

**`format_customer_list`** — 客户列表 `erp.query.customers.list`：
```python
_CUSTOMER_LABELS = {
    "name": "名称", "code": "编码",
    "type": "类型",  # ← 新增（0=分销商,1=经销商,2=线下渠道,3=其他,4=线上代发）
    "level": "等级",  # ← 新增（1~5级）
    "contact": "联系人", "contactPhone": "电话",
    "discountRate": "折扣率",  # ← 新增
    "status": "状态",
    "remark": "备注",  # ← 新增
    "invoiceTitle": "发票抬头",  # ← 新增
}
_CUSTOMER_TRANSFORMS = {
    "type": lambda v: {0:"分销商",1:"经销商",2:"线下渠道",3:"其他",4:"线上代发"}.get(v, str(v)),
    "status": lambda v: "正常" if v == 1 else "停用",
}
```

**`format_distributor_list`** — 分销商列表 `erp.distributor.list.query`：
```python
_DISTRIBUTOR_LABELS = {
    "distributorCompanyName": "公司名称",
    "distributorCompanyId": "公司ID",
    "distributorLevel": "等级",
    "saleStaffName": "业务员",
    "showState": "状态",  # ← 新增（1=待审核,2=已生效,3=已作废,4=已拒绝）
    "purchaseAccount": "采购账户",  # ← 新增
    "helpMsg": "助记符",  # ← 新增
    "remark": "备注",  # ← 新增
    "autoSyncStock": "自动同步库存",  # ← 新增
}
_DISTRIBUTOR_TRANSFORMS = {
    "showState": lambda v: {1:"待审核",2:"已生效",3:"已作废",4:"已拒绝"}.get(v, str(v)),
    "autoSyncStock": lambda v: "是" if v else "否",
}
```

---

### warehouse.py（10个formatter）

**`format_allocate_list`** — 调拨单列表 `erp.allocate.task.query`：

> **关键修正**：3个核心字段名全错。

```python
_ALLOCATE_LABELS = {
    "code": "调拨单号",  # ← 修正（非allocateNo/orderNo）
    "shortId": "短号",
    "status": "状态",
    "outWarehouseName": "调出仓",  # ← 修正（非fromWarehouseName）
    "inWarehouseName": "调入仓",  # ← 修正（非toWarehouseName）
    "outNum": "申请数量",  # ← 新增
    "actualOutNum": "实际出库",  # ← 新增
    "inNum": "实际入库",  # ← 新增
    "diffNum": "差异数量",  # ← 新增
    "outTotalAmount": "调拨金额",  # ← 新增
    "inTotalAmount": "入库金额",  # ← 新增
    "diffAmount": "差异金额",  # ← 新增
    "creatorName": "创建人",  # ← 新增
    "created": "创建时间",
    "labelName": "标签",  # ← 新增
}
_ALLOCATE_TRANSFORMS = {
    "created": format_timestamp,
    "outTotalAmount": lambda v: f"¥{v}" if v else "",
    "inTotalAmount": lambda v: f"¥{v}" if v else "",
}
```

**`format_allocate_detail`** — 调拨单明细 `erp.allocate.task.detail.query`：

> **关键修正**：items中 `title` 不存在（用 `itemOuterId`+`outerId`），`num` 不存在（用 `outNum`）。

```python
_ALLOCATE_DETAIL_LABELS = {
    "itemOuterId": "主编码",  # ← 修正（非title）
    "outerId": "SKU编码",
    "outNum": "申请数量",  # ← 修正（非num/quantity）
    "actualOutNum": "实际出库",  # ← 新增
    "inNum": "实际入库",  # ← 新增
    "price": "成本价",  # ← 新增
    "diffNum": "差异数量",  # ← 新增
    "diffAmount": "差异金额",  # ← 新增
    "actualOutTotalAmount": "出库金额",  # ← 新增
    "inTotalAmount": "入库金额",  # ← 新增
    "refundNum": "拒收数量",  # ← 新增
    "batchNo": "批次号", "productTime": "生产日期", "expireDate": "有效期",
    "remark": "备注",
}
_ALLOCATE_DETAIL_TRANSFORMS = {
    "price": lambda v: f"¥{v}" if v else "",
}
```

**`format_other_in_out_list`** — 入出库单 `other.in.order.query` / `other.out.order.query`：

> **关键修正**：单号字段是 `code`，非 `orderNo`。

```python
_OTHER_IO_LABELS = {
    "code": "单号",  # ← 修正（非orderNo）
    "shortId": "短号",
    "customType": "出入库类型",  # ← 新增
    "busyTypeDesc": "业务类型",  # ← 新增
    "status": "状态",
    "statusName": "状态名",  # ← 新增（出库单有此字段，人可读）
    "warehouseName": "仓库",
    "supplierName": "供应商",  # ← 新增
    "purchaseOrderCode": "关联采购单",  # ← 新增（入库单特有）
    "quantity": "总数量",  # ← 新增
    "getGoodNum": "良品数", "getBadNum": "次品数",  # ← 新增
    "shelvedQuantity": "已上架", "waitShelveQuantity": "待上架",  # ← 新增
    "totalDetailFee": "总金额",  # ← 新增（单位：分）
    "createrName": "创建人",  # ← 新增
    "created": "创建时间",
    "remark": "备注",
}
_OTHER_IO_TRANSFORMS = {
    "created": format_timestamp,
    "totalDetailFee": lambda v: f"¥{v/100:.2f}" if v else "",
}
```

**`format_inventory_sheet_list`** — 盘点单列表 `inventory.sheet.query`：

> **关键修正**：单号字段是 `code`，非 `sheetNo`/`orderNo`。

```python
_INV_SHEET_LABELS = {
    "code": "盘点单号",  # ← 修正（非sheetNo/orderNo）
    "warehouseName": "仓库",
    "type": "类型",  # ← 新增（1=正常盘点,2=即时盘点）
    "status": "状态",
    "createdName": "创建人",  # ← 新增
    "created": "创建时间",
    "submitName": "提交人", "submitted": "提交时间",  # ← 新增
    "audditName": "审核人", "auddited": "审核时间",  # ← 新增
    "remark": "备注",  # ← 新增
}
_INV_SHEET_TRANSFORMS = {
    "type": lambda v: {1:"正常盘点",2:"即时盘点"}.get(v, str(v)),
    "status": lambda v: {1:"待提交",2:"待审核",3:"已审核",4:"已作废"}.get(v, str(v)),
    "created": format_timestamp,
    "submitted": format_timestamp,
    "auddited": format_timestamp,
}
```

**`format_inventory_sheet_detail`** — 盘点单明细 `inventory.sheet.get`：

> **关键修正**：3个核心数量字段名全错。

```python
_INV_SHEET_DETAIL_LABELS = {
    "sheetCode": "盘点单号",
    "title": "名称",
    "outerId": "编码",
    "propertiesName": "规格",  # ← 新增（区分SKU）
    "beforeNum": "系统数",  # ← 修正（非sysQuantity/systemNum）
    "afterNum": "实盘数",  # ← 修正（非realQuantity/realNum）
    "differentNum": "差异数",  # ← 修正（非diffQuantity/diffNum）
    "differentAmount": "差异金额",  # ← 新增
    "qualityType": "品质",  # ← 新增（0=次品,1=良品）
    "inventoryName": "盘点人",  # ← 新增
    "inventoryTime": "盘点时间",  # ← 新增
    "goodsSectionCode": "货位",  # ← 新增
}
_INV_SHEET_DETAIL_TRANSFORMS = {
    "qualityType": lambda v: "良品" if v == 1 else "次品",
    "differentAmount": lambda v: f"¥{v}" if v else "",
    "inventoryTime": format_timestamp,
}
```

**其余4个formatter**（下架单/货位库存/加工单/批次库存/货位进出记录）：转为标签映射表，字段名待实际API响应确认。

---

### purchase.py（7个formatter）

**`format_supplier_list`** — 供应商列表 `supplier.list.query`：

> **关键修正**：联系人字段是 `contactName`，非 `contact`。

```python
_SUPPLIER_LABELS = {
    "name": "名称",  # API字段是 name（非supplierName）
    "code": "编码",  # API字段是 code（非supplierCode）
    "status": "状态",
    "contactName": "联系人",  # ← 修正（非contact）
    "mobile": "手机", "phone": "电话",
    "email": "邮箱",  # ← 新增
    "categoryName": "供应商分类",  # ← 新增
    "billType": "结算方式",  # ← 新增
    "planReceiveDay": "预计交期(天)",  # ← 新增
    "address": "地址",  # ← 新增
    "remark": "备注",  # ← 新增
}
_SUPPLIER_TRANSFORMS = {
    "status": lambda v: {0:"停用",1:"正常"}.get(v, str(v)),
}
```

**`format_purchase_order_list`** — 采购单列表 `purchase.order.query`：

> **关键修正**：单号字段是 `code`，非 `purchaseNo`/`orderNo`。

```python
_PURCHASE_ORDER_LABELS = {
    "code": "采购单号",  # ← 修正（非purchaseNo/orderNo）
    "shortId": "短号",
    "supplierName": "供应商",
    "status": "状态",
    "totalAmount": "总金额", "actualTotalAmount": "实际金额",  # ← 新增
    "quantity": "总数量",  # ← 新增
    "arrivedQuantity": "已到货",  # ← 新增
    "receiveQuantity": "已收货",  # ← 新增
    "receiveWarehouseName": "收货仓库",  # ← 新增
    "deliveryDate": "交货日期",  # ← 新增
    "createrName": "创建人",  # ← 新增
    "created": "创建时间",
    "remark": "备注",  # ← 新增
    "financeStatus": "财务状态",  # ← 新增
}
_PURCHASE_ORDER_TRANSFORMS = {
    "totalAmount": lambda v: f"¥{v}" if v else "",
    "actualTotalAmount": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
    "deliveryDate": format_timestamp,
}
```

**`format_purchase_order_detail`** — 采购单明细 `purchase.order.get`：

> **关键修正**：数量字段是 `count`，非 `num`/`quantity`。

```python
_PURCHASE_DETAIL_LABELS = {
    "itemOuterId": "主编码",  # ← 修正（非title/itemTitle，API不返回title）
    "outerId": "SKU编码",
    "count": "数量",  # ← 修正（非num/quantity）
    "price": "单价",
    "amount": "金额（调前）",  # ← 新增
    "totalFee": "金额（调后）",  # ← 新增
    "amendAmount": "调整金额",  # ← 新增
    "deliveryDate": "交货日期",  # ← 新增
    "remark": "备注",
}
_PURCHASE_DETAIL_TRANSFORMS = {
    "price": lambda v: f"¥{v}" if v else "",
    "amount": lambda v: f"¥{v}" if v else "",
    "totalFee": lambda v: f"¥{v}" if v else "",
    "deliveryDate": format_timestamp,
}
```

**`format_purchase_return_list`** — 采退单列表 `purchase.return.list.query`：

> **关键修正**：单号是 `code`（非 `returnNo`），时间是 `gmCreate`（非 `created`）。

```python
_PURCHASE_RETURN_LABELS = {
    "code": "采退单号",  # ← 修正（非returnNo/orderNo）
    "supplierName": "供应商",
    "status": "状态",
    "statusName": "状态名",  # ← 新增（人可读中文状态）
    "totalAmount": "总金额",  # ← 新增
    "totalCount": "总数量",  # ← 新增
    "returnNum": "退货数量",  # ← 新增
    "actualReturnNum": "实退数量",  # ← 新增
    "warehouseName": "仓库",  # ← 新增
    "createrName": "创建人",  # ← 新增
    "gmCreate": "创建时间",  # ← 修正（非created）
    "financeStatus": "财务状态",  # ← 新增
}
_PURCHASE_RETURN_TRANSFORMS = {
    "totalAmount": lambda v: f"¥{v}" if v else "",
    "gmCreate": format_timestamp,
}
```

**`format_warehouse_entry_list`** — 收货单列表 `other.in.order.query`（采购入库）：

> **关键修正**：单号是 `code`，非 `entryNo`/`orderNo`。

```python
_WH_ENTRY_LABELS = {
    "code": "收货单号",  # ← 修正（非entryNo/orderNo）
    "purchaseOrderCode": "关联采购单",  # ← 新增
    "supplierName": "供应商",
    "warehouseName": "仓库",
    "status": "状态",
    "quantity": "总数量",  # ← 新增
    "receiveQuantity": "已收货",  # ← 新增
    "shelvedQuantity": "已上架",  # ← 新增
    "getGoodNum": "良品数", "getBadNum": "次品数",  # ← 新增
    "totalDetailFee": "总金额",  # ← 新增
    "createrName": "创建人",  # ← 新增
    "created": "创建时间",
    "busyTypeDesc": "业务类型",  # ← 新增
}
_WH_ENTRY_TRANSFORMS = {
    "created": format_timestamp,
    "totalDetailFee": lambda v: f"¥{v/100:.2f}" if v else "",
}
```

**`format_purchase_strategy`** — 采购建议 `sale.purchase.strategy.query`：

> **关键修正**：3个核心字段名全错。API不返回 `title`/`suggestNum`/`availableStock`。

```python
_STRATEGY_LABELS = {
    "itemOuterId": "主编码",  # ← 修正（非title/itemTitle，API不返回title）
    "outerId": "SKU编码",
    "propertiesName": "规格",  # ← 新增
    "purchaseStock": "建议采购数",  # ← 修正（非suggestNum/purchaseNum）
    "stockoutNum": "缺货数",  # ← 修正（非availableStock/stock）
    "itemCatName": "分类",  # ← 新增
}
```

**`format_shelf_list`** — 上架单列表：转为标签映射表，字段名待确认。

---

### aftersales.py（6个formatter）

**`_format_aftersale_item`** — 售后工单 `erp.aftersale.list.query`：

> **关键修正**：工单号是 `id`（非 `refundId`/`workOrderNo`），退款金额是 `refundMoney`（非 `refundFee`）。

```python
_AFTERSALE_LABELS = {
    "id": "工单号",  # ← 修正（非refundId/workOrderNo）
    "shortId": "短号",
    "tid": "订单号", "sid": "系统单号",
    "afterSaleType": "类型",
    "status": "状态",
    "buyerNick": "买家",
    "buyerName": "买家姓名", "buyerPhone": "买家电话",  # ← 新增
    "shopName": "店铺", "source": "平台",  # ← 新增
    "refundMoney": "系统退款",  # ← 修正（非refundFee/amount）
    "rawRefundMoney": "平台实退",  # ← 新增
    "refundPostFee": "退运费",  # ← 新增
    "goodStatus": "货物状态",  # ← 新增（1~4）
    "textReason": "原因",
    "refundWarehouseName": "退货仓库",  # ← 新增
    "refundExpressCompany": "退回快递",  # ← 新增
    "refundExpressId": "退回单号",  # ← 新增
    "platformId": "平台售后单号",  # ← 新增
    "reissueSid": "补发/换货订单号",  # ← 新增
    "remark": "备注",
    "created": "创建时间",
    "finished": "完成时间",  # ← 新增
}
_AFTERSALE_TRANSFORMS = {
    "afterSaleType": lambda v: {1:"退款",2:"退货",3:"补发",4:"换货",5:"发货前退款"}.get(v, str(v)),
    "status": lambda v: {1:"未分配",2:"未解决",3:"优先退款",4:"同意",5:"拒绝",
                         6:"确认退货",7:"确认发货",8:"确认退款",9:"处理完成",10:"作废"}.get(v, str(v)),
    "goodStatus": lambda v: {1:"买家未发",2:"买家已发",3:"卖家已收",4:"无需退货"}.get(v, str(v)),
    "refundMoney": lambda v: f"¥{v}" if v else "",
    "rawRefundMoney": lambda v: f"¥{v}" if v else "",
    "refundPostFee": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
    "finished": format_timestamp,
}
```

售后工单嵌套商品（`items[]`）：
```python
_AFTERSALE_ITEM_LABELS = {
    "title": "商品", "mainOuterId": "主编码", "outerId": "编码",
    "propertiesName": "规格",
    "receivableCount": "申请数", "itemRealQty": "实退数",
    "price": "单价", "payment": "实付",
    "type": "处理方式",  # 1=退货,2=补发
    "goodItemCount": "良品数", "badItemCount": "次品数",
}
```

**`format_refund_warehouse`** — 销退入库单 `erp.aftersale.refund.warehouse.query`：

> **关键修正**：单号是 `id`（非 `orderNo`/`refundNo`），仓库字段大小写是 `wareHouseName`。

```python
_REFUND_WH_LABELS = {
    "id": "入库单号",  # ← 修正（非orderNo/refundNo）
    "workOrderId": "售后工单号",  # ← 新增
    "sid": "系统单号", "tid": "订单号",  # ← 新增
    "afterSaleTypeName": "售后类型",  # ← 新增
    "wareHouseName": "收货仓库",  # ← 修正（注意大小写，非warehouseName）
    "status": "状态",
    "receiveUser": "收货人",  # ← 新增
    "receiveGoodsTime": "收货时间",  # ← 新增
    "expressName": "退回快递",  # ← 新增
    "expressId": "退回快递号",  # ← 新增
    "endTime": "完成时间",  # ← 新增
}
_REFUND_WH_TRANSFORMS = {
    "status": lambda v: {1:"待入库",2:"部分入库",3:"已完成",4:"已取消",5:"已作废"}.get(v, str(v)),
    "receiveGoodsTime": format_timestamp,
    "endTime": format_timestamp,
}
```

**`format_replenish_list`** — 登记补款 `erp.aftersale.replenish.list.query`：

> **关键修正**：金额字段是 `refundMoney`，非 `amount`/`money`。

```python
_REPLENISH_LABELS = {
    "tid": "订单号", "sid": "系统单号",  # ← 新增
    "shopName": "店铺",  # ← 新增
    "afterSaleType": "售后类型",  # ← 新增
    "refundMoney": "补款金额",  # ← 修正（非amount/money）
    "status": "状态",
    "urgency": "紧急程度",  # ← 新增
    "sysMaker": "创建人",  # ← 新增
    "responsiblePerson": "责任人",  # ← 新增
    "replenishRemark": "备注",  # ← 新增
    "created": "创建时间",
}
_REPLENISH_TRANSFORMS = {
    "refundMoney": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
}
```

**`format_repair_list`** — 维修单列表 `erp.aftersale.repair.list.query`：

> **关键修正**：全部3个核心字段名错误。

```python
_REPAIR_LABELS = {
    "repairOrderNum": "维修单号",  # ← 修正（非repairNo/orderNo）
    "repairStatus": "状态",  # ← 修正（非status）
    "userNick": "用户",  # ← 修正（非customerName）
    "sid": "系统单号", "tid": "订单号",  # ← 新增
    "shopName": "店铺",  # ← 新增
    "contactInfo": "联系方式",  # ← 新增
    "repairMoney": "维修费用",  # ← 新增
    "repairWarehouseName": "维修仓库",  # ← 新增
    "problemDescription": "问题描述",  # ← 新增
    "failureCause": "故障原因",  # ← 新增
    "created": "创建时间",
    "finishTime": "完成时间",  # ← 新增
}
_REPAIR_TRANSFORMS = {
    "repairStatus": lambda v: {0:"待受理",1:"维修中",2:"待出库",3:"已完成",4:"已拒绝",-1:"已作废"}.get(v, str(v)),
    "repairMoney": lambda v: f"¥{v}" if v else "",
    "created": format_timestamp,
    "finishTime": format_timestamp,
}
```

**`format_repair_detail`** — 维修单详情 `erp.aftersale.repair.detail.query`：

> **结构修正**：API返回 `{order: {...}, itemList: [...], feeList: [...], partsList: [...]}`，不是扁平结构。

```python
# 响应结构: data.order（基本信息）+ data.itemList（维修商品）+ data.feeList（费用）
# 需要特殊处理，不能直接用 format_item_with_labels

_REPAIR_DETAIL_ITEM_LABELS = {
    "repairItemName": "商品名",  # ← 修正（非title）
    "repairItemCode": "编码",
    "specification": "规格",
    "repairQuantity": "数量",  # ← 修正（非num）
    "identificationCode": "识别码",
    "problemDescription": "问题描述",
}
_REPAIR_DETAIL_FEE_LABELS = {
    "currentPrice": "费用", "receivedWay": "入账途径",
    "operatorName": "操作人", "operatorTime": "操作时间",
}
```

**`format_aftersale_log`** — 售后操作日志 `erp.aftersale.operate.log.query`：

> **关键修正**：全部4个字段名与API不符。

```python
_AFTERSALE_LOG_LABELS = {
    "key": "工单号",  # ← 新增
    "operateTime": "时间",  # ← 修正（非operTime）
    "operateType": "操作类型",
    "content": "操作内容",  # ← 修正（非action）
    "staffName": "操作人账号",  # ← 修正（非operName）
    "operateName": "操作人",  # ← 修正（非operator）
}
_AFTERSALE_LOG_TRANSFORMS = {"operateTime": format_timestamp}
```

---

### qimen.py（2个formatter）

保留 `_ORDER_TYPE_MAP`（15种）/ `_REFUND_TYPE_MAP`（5种）/ `_REFUND_STATUS_MAP`（10种）。

**`_format_taobao_order`** — 补全收件人/物流字段：
```python
# 在现有字段基础上新增：
_QIMEN_ORDER_EXTRA_LABELS = {
    "outSid": "快递单号",
    "receiverName": "收件人", "receiverMobile": "电话",
    "receiverState": "省", "receiverCity": "市",
    "receiverAddress": "地址",
    "postFee": "运费",
    "sellerMemo": "卖家备注", "buyerMessage": "买家留言",
    "consignTime": "发货时间",
}
```

**`_format_taobao_refund`** — 补全退款/物流字段：
```python
# 与 _AFTERSALE_LABELS 共用同类字段，在现有基础上新增：
_QIMEN_REFUND_EXTRA = {
    "buyerName": "买家姓名", "buyerPhone": "买家电话",
    "goodStatus": "货物状态",
    "rawRefundMoney": "平台实退",
    "refundPostFee": "退运费",
    "refundWarehouseName": "退货仓库",
    "refundExpressCompany": "退回快递", "refundExpressId": "退回单号",
    "platformId": "平台售后单号",
    "source": "平台",
    "finished": "完成时间",
    "reissueSid": "补发订单号",
}
```

保留嵌套 orders/items 逻辑。

---

## 字段名错误汇总（必须修正）

| 模块 | 现有代码用的字段 | API实际字段 | 影响 |
|------|-----------------|------------|------|
| warehouse | `allocateNo`/`orderNo` | `code` | 调拨单号为空 |
| warehouse | `fromWarehouseName` | `outWarehouseName` | 调出仓为空 |
| warehouse | `toWarehouseName` | `inWarehouseName` | 调入仓为空 |
| warehouse | `sysQuantity`/`systemNum` | `beforeNum` | 盘点系统数为空 |
| warehouse | `realQuantity`/`realNum` | `afterNum` | 盘点实盘数为空 |
| warehouse | `diffQuantity`/`diffNum` | `differentNum` | 盘点差异为空 |
| warehouse | `sheetNo`/`orderNo` | `code` | 盘点单号为空 |
| purchase | `purchaseNo`/`orderNo` | `code` | 采购单号为空 |
| purchase | `num`/`quantity`(detail) | `count` | 采购数量为空 |
| purchase | `returnNo`/`orderNo` | `code` | 采退单号为空 |
| purchase | `entryNo`/`orderNo` | `code` | 收货单号为空 |
| purchase | `suggestNum`/`purchaseNum` | `purchaseStock` | 建议采购数为空 |
| purchase | `availableStock`/`stock` | `stockoutNum` | 缺货数为空 |
| purchase | `contact`(supplier) | `contactName` | 联系人为空 |
| aftersales | `refundId`/`workOrderNo` | `id` | 售后工单号为空 |
| aftersales | `refundFee`/`amount` | `refundMoney` | 退款金额为空 |
| aftersales | `operTime` | `operateTime` | 日志时间为空 |
| aftersales | `action`(log) | `content` | 日志内容为空 |
| aftersales | `operName` | `staffName` | 日志操作人为空 |
| aftersales | `repairNo`/`orderNo` | `repairOrderNum` | 维修单号为空 |
| aftersales | `customerName` | `userNick` | 维修客户为空 |
| trade | `receiverProvince` | `receiverState` | 省份为空 |

---

## 幽灵字段（API中不存在，必须移除）

| 模块 | 字段 | 说明 |
|------|------|------|
| `_WH_STOCK_LABELS`(原方案) | `purchaseNum` | `erp.item.warehouse.list.get` 不返回此字段（仅在 `stock.api.status.query` 中有） |
| `_PRODUCT_LABELS`(原方案) | `sellingPrice` | `item.list.query` 的销售价字段名是 `priceOutput`（`sellingPrice` 仅在库存查询中有） |

---

## 结构不匹配（需要重写数据读取逻辑）

| Formatter | 现有逻辑 | API实际结构 | 修复方式 |
|-----------|---------|------------|---------|
| `format_express_list` | `data.get("list")` 取列表 | 扁平结构 `{cpCode, outSids[], expressName}` | 重写为直接读取扁平字段 |
| `format_outstock_order_list` | 嵌套用 `item.get("orders")` | `erp.wave.logistics.order.query` 用 `details[]` | 兼容 `orders` 和 `details` 两种键 |
| `format_repair_detail` | 扁平读取 `data.get("items")` | 嵌套结构 `{order:{}, itemList:[], feeList:[], partsList:[]}` | 重写为分别读取 order/itemList/feeList |

---

## 文件清单

| 文件 | 操作 |
|------|------|
| `formatters/common.py` | 新增 `format_item_with_labels()` + `_GLOBAL_SKIP` |
| `formatters/product.py` | 6个formatter重构 + 补全6个高价值字段 |
| `formatters/trade.py` | 6个formatter重构 + 修正省份字段名 + 补全17个字段 + 修复express/logistics结构 |
| `formatters/basic.py` | 5个formatter重构 + 补全店铺到期/客户等级/分销商状态等 |
| `formatters/warehouse.py` | 10个formatter重构 + 修正9处字段名错误 |
| `formatters/purchase.py` | 7个formatter重构 + 修正8处字段名错误 |
| `formatters/aftersales.py` | 6个formatter重构 + 修正8处字段名错误 + 修复repair_detail结构 |
| `formatters/qimen.py` | 2个formatter重构 + 补全收件人/物流字段 |

---

## 实施顺序建议

1. **common.py** — 先部署核心函数
2. **product.py** — 影响最大（库存查询是高频场景）
3. **trade.py** — 订单查询高频
4. **aftersales.py** — 字段名错误最多，修复后改善最明显
5. **purchase.py** — 字段名错误多
6. **warehouse.py** — 字段名错误多
7. **basic.py** — 影响较小
8. **qimen.py** — 影响最小

## 附：路由提示词更新（5B完成后一并更新）

**文件**：`backend/config/erp_tools.py`

降级策略部分更新：
```
- 编码查询系统会自动提取基础编码宽泛查询+翻页拉取全量数据+本地匹配，无需手动重试
- stock_status 已包含采购在途(purchaseNum)/调拨/残次品/销退在途/虚拟库存等完整库存字段，查库存相关数据用 stock_status 即可
```

stock_status 的 description 补充：
```
查询库存数量和状态（总库存/可售/实际可用/锁定/采购在途/销退在途/调拨/残次品/虚拟库存，含各仓汇总）
```

## 验证
```bash
cd /Users/wucong/EVERYDAYAIONE/backend && source venv/bin/activate
python -m pytest tests/ -q --tb=short  # 全量回归
```

部署后实测：
1. `SEVENTEENLSG01-01 查库存` — 应显示采购在途、销退在途、实际可用
2. `查订单详情` — 应显示快递单号、收件省份、运费
3. `查采购单` — 采购单号不应为空
4. `查售后工单` — 工单号/退款金额不应为空
5. `查调拨单` — 调出仓/调入仓不应为空
6. `查盘点单明细` — 系统数/实盘数不应为空

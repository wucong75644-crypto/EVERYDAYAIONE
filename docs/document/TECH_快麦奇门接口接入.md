# 技术设计：快麦奇门自定义接口接入

## 1. 现有代码分析

### 已阅读文件及关键理解

| 文件 | 关键理解 |
|-----|---------|
| `services/kuaimai/client.py` | HTTP客户端，HMAC签名，Token自动刷新。`request()` 固定发到 `self._base_url`，需扩展支持不同网关 |
| `services/kuaimai/registry/base.py` | `ApiEntry` 数据结构，已有 `page_size` 字段但无 `base_url` |
| `services/kuaimai/registry/trade.py` | ERP订单用 `erp.trade.list.query`，参数为 `timeType(str)` / `sysStatus`，响应 key=`list` |
| `services/kuaimai/dispatcher.py` | 统一调度引擎：查注册表 → `map_params` → 调client → 格式化。仅传 `method` + `biz_params` |
| `services/kuaimai/param_mapper.py` | 参数映射 + 日期标准化 + 分页默认值，已支持 `pageSize`/`pageNo` |
| `services/kuaimai/formatters/trade.py` | `_format_order()` 取 `tid/sid/sysStatus/buyerNick/payment/shopName`，可复用 |
| `services/kuaimai/formatters/aftersales.py` | `_format_aftersale_item()` 取 `tid/refundId/afterSaleType/status`，可部分复用 |
| `config/erp_tools.py` | 7个ERP工具定义，`ERP_SYNC_TOOLS` 集合驱动自动注册，`ERP_ROUTING_PROMPT` 含路由策略 |
| `config/agent_tools.py` | 导入 `ERP_SYNC_TOOLS` + `build_erp_tools()` 自动组装工具列表，无需手动改 |
| `services/tool_executor.py` | 遍历 `ERP_SYNC_TOOLS` 自动注册handler，委托 `ErpDispatcher`，无需手动改 |
| `core/config.py` | 快麦配置区域 `kuaimai_*`，需新增奇门相关配置 |

### 可复用模块
- **签名算法**：奇门接口使用相同的 HMAC-MD5 签名，`KuaiMaiClient.generate_sign()` 完全复用
- **参数映射器**：`param_mapper.map_params()` 的日期标准化 + 分页处理完全兼容
- **调度引擎**：`ErpDispatcher.execute()` 的 查表→映射→调用→格式化 流程复用
- **自动注册**：只要新工具名加入 `ERP_SYNC_TOOLS`，`tool_executor.py` 和 `agent_tools.py` 自动接入
- **格式化函数**：`_format_order()` 可直接复用（奇门和ERP的订单字段结构一致）

### 设计约束
- 奇门和ERP使用**同一套 appKey/appSecret/accessToken**，但走不同网关
- 奇门订单和售后有**不同的请求域名**（淘宝按方法分配独立域名）
- 必须兼容现有 `KuaiMaiClient` 的 Token 刷新机制（刷新仍走ERP网关）

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| `ApiEntry` 新增 `base_url` 字段 | `dispatcher.py` | 调用 client 时传入 `base_url` 覆盖 |
| `KuaiMaiClient.request()` 新增 `base_url` + `extra_system_params` 参数 | `dispatcher.py` | 传入 entry 的 base_url 和系统参数 |
| `ERP_SYNC_TOOLS` 新增工具名 | `agent_tools.py`(自动) / `tool_executor.py`(自动) | 无需手动改 |
| `TOOL_REGISTRIES` 新增映射 | `registry/__init__.py` | 导入 `QIMEN_REGISTRY` |
| `_FORMATTER_REGISTRY` 新增 | `formatters/__init__.py` | 导入 `QIMEN_FORMATTERS` |
| `config.py` 新增配置项 | `.env.example` | 同步示例配置 |

---

## 2. 奇门 vs ERP API 差异对照

| 维度 | ERP API | 奇门自定义接口 |
|-----|---------|--------------|
| 网关 | `gw.superboss.cc/router` | `*.api.taobao.com/router/qm`（每个方法独立域名） |
| 订单方法 | `erp.trade.list.query` | `kuaimai.order.list.query` |
| 售后方法 | 分散多个方法 | `kuaimai.refund.list.query` |
| 时间参数 | `timeType` (字符串: created/pay_time) | `dateType` (整数: 0修改/1创建/2下单/3发货) |
| 店铺筛选 | `shopName` (名称) | `userId` (店铺编号Long) |
| 订单状态 | 4种 (WAIT_AUDIT等) | 12种 (含WAIT_BUYER_PAY/WAIT_FINANCE_AUDIT等) |
| 订单类型 | 无 | `types` 支持60+种类型枚举 |
| 响应 key | `list` | `trades` / `workOrders` |
| 分页字段 | `total` + `list` | `total` + `hasNextPage` + `pageNo` + `pageSize` |
| 额外系统参数 | 无 | `target_app_key=23204092` + `customerId`(商家路由) |
| 签名 | HMAC-MD5 ✅同 | HMAC-MD5 ✅同 |

---

## 3. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 奇门未配置（缺 customerId） | 返回友好提示 "淘宝奇门未配置，请设置 QIMEN_CUSTOMER_ID" | `dispatcher.py` |
| ERP配置好但奇门没配 | 两套独立校验，ERP工具正常，奇门工具返回配置缺失提示 | `dispatcher.py` |
| 网络超时/淘宝网关不可达 | 复用 KuaiMaiClient 的 tenacity 重试（3次指数退避） | `client.py` |
| Token 过期 | 复用现有 Token 自动刷新机制（刷新走ERP网关，奇门共享 Token） | `client.py` |
| 查询结果过多 | formatter 限制前20条显示 + total 总数提示 | `formatters/qimen.py` |
| 空结果 | 返回 "未找到符合条件的淘宝订单/售后单" | `formatters/qimen.py` |
| 无效 customerId | 淘宝网关返回错误 → `KuaiMaiBusinessError` → 回传大脑 | `client.py` / `dispatcher.py` |
| AI 传了不存在的 action | dispatcher 返回可选 action 列表 | `dispatcher.py`（已有逻辑） |
| 日期格式 yyyy-MM-dd | `param_mapper._normalize_dates()` 自动补全时间部分 | `param_mapper.py`（已有逻辑） |

---

## 4. 技术栈
- 后端：Python 3.12 + FastAPI
- HTTP：httpx（复用现有 KuaiMaiClient）
- 重试：tenacity（复用现有策略）
- 无新增依赖

---

## 5. 目录结构

### 新增文件（2个）
```
backend/services/kuaimai/registry/qimen.py   # 奇门API注册表（2个ApiEntry）
backend/services/kuaimai/formatters/qimen.py  # 奇门格式化器（2个formatter）
```

### 修改文件（7个）
```
backend/services/kuaimai/registry/base.py      # ApiEntry 新增 base_url + system_params
backend/services/kuaimai/registry/__init__.py   # 导入 QIMEN_REGISTRY + 添加 TOOL_REGISTRIES 映射
backend/services/kuaimai/client.py              # request() 支持 base_url 覆盖 + extra_system_params
backend/services/kuaimai/dispatcher.py          # 传入 entry.base_url 和 system_params
backend/services/kuaimai/formatters/__init__.py # 注册 QIMEN_FORMATTERS
backend/config/erp_tools.py                     # 新增 erp_taobao_query 工具定义 + 路由提示词
backend/core/config.py                          # 新增 qimen_* 配置项
backend/.env.example                            # 新增奇门配置示例
```

### 无需修改（自动接入）
```
backend/config/agent_tools.py     # 导入 ERP_SYNC_TOOLS + build_erp_tools() → 自动包含新工具
backend/services/tool_executor.py # 遍历 ERP_SYNC_TOOLS 自动注册 handler → 自动调度
backend/services/agent_loop.py    # 无任何改动
```

---

## 6. 数据库设计

无新增表。

---

## 7. 详细设计

### 7.1 ApiEntry 扩展（registry/base.py）

```python
@dataclass(frozen=True)
class ApiEntry:
    method: str
    description: str
    param_map: Dict[str, str] = field(default_factory=dict)
    required_params: List[str] = field(default_factory=list)
    defaults: Dict[str, Any] = field(default_factory=dict)
    formatter: str = "format_generic_list"
    response_key: Optional[str] = "list"
    page_size: int = 20
    is_write: bool = False
    confirm_template: Optional[str] = None
    # ── 新增 ──
    base_url: Optional[str] = None           # 网关地址覆盖（奇门用）
    system_params: Dict[str, Any] = field(default_factory=dict)  # 额外系统参数
```

### 7.2 奇门注册表（registry/qimen.py）

```python
QIMEN_REGISTRY = {
    "order_list": ApiEntry(
        method="kuaimai.order.list.query",
        description="淘宝订单列表查询",
        base_url="http://33c367ryyg.api.taobao.com/router/qm",
        system_params={"target_app_key": "23204092"},
        response_key="trades",
        param_map={
            "tid": "tid",
            "sid": "sid",
            "status": "status",
            "date_type": "dateType",
            "shop_id": "userId",
            "warehouse_id": "warehouseId",
            "start_date": "startTime",
            "end_date": "endTime",
            "types": "types",
            "tag_ids": "tagIds",
        },
        formatter="format_qimen_order_list",
    ),
    "refund_list": ApiEntry(
        method="kuaimai.refund.list.query",
        description="淘宝售后单列表查询",
        base_url="http://z29932hpkn.api.taobao.com/router/qm",
        system_params={"target_app_key": "23204092"},
        response_key="workOrders",
        param_map={
            "tid": "tid",
            "refund_id": "id",
            "refund_type": "refundType",
            "shop_id": "userId",
            "warehouse_id": "warehouseId",
            "start_date": "startTime",
            "end_date": "endTime",
        },
        formatter="format_qimen_refund_list",
    ),
}
```

### 7.3 KuaiMaiClient 扩展（client.py）

`request()` 方法签名变更：

```python
async def request(
    self,
    method: str,
    biz_params: Optional[Dict[str, Any]] = None,
    sign_method: str = "hmac",
    *,
    base_url: Optional[str] = None,          # 新增：网关覆盖
    extra_system_params: Optional[Dict[str, Any]] = None,  # 新增：额外系统参数
) -> Dict[str, Any]:
```

变更点：
1. `common_params` 合并 `extra_system_params`（如 `target_app_key`, `customerId`）
2. POST 地址用 `base_url or self._base_url`
3. `request_with_retry()` 透传这两个参数

### 7.4 ErpDispatcher 扩展（dispatcher.py）

`execute()` 变更：

```python
# 构建系统参数（合并 entry 静态配置 + 运行时 customerId）
system_params = dict(entry.system_params)
if entry.base_url:
    # 奇门接口需要 customerId
    from core.config import settings
    customer_id = settings.qimen_customer_id
    if not customer_id:
        return "淘宝奇门未配置，请在 .env 中设置 QIMEN_CUSTOMER_ID"
    system_params["customerId"] = customer_id

# 调用API
data = await self._client.request_with_retry(
    entry.method,
    api_params,
    base_url=entry.base_url,
    extra_system_params=system_params or None,
)
```

### 7.5 工具定义（erp_tools.py）

新增第8个工具 `erp_taobao_query`：

```python
_build_query_tool(
    "erp_taobao_query",
    (
        "查询淘宝/天猫平台的订单和售后单（通过奇门接口）。"
        "返回 {total, trades/workOrders[]}。"
        "page_size=1 可只取计数。支持 shop_id 按店铺筛选。"
    ),
    QIMEN_REGISTRY,
    {
        "tid": {"type": "string", "description": "平台订单号"},
        "sid": {"type": "string", "description": "系统订单号"},
        "status": {
            "type": "string",
            "description": (
                "订单状态: WAIT_BUYER_PAY(待付款), WAIT_AUDIT(待审核), "
                "WAIT_FINANCE_AUDIT(待财审), FINISHED_AUDIT(审核完成), "
                "WAIT_SEND_GOODS(待发货), SELLER_SEND_GOODS(已发货), "
                "FINISHED(交易完成), CLOSED(交易关闭)"
            ),
        },
        "date_type": {
            "type": "integer",
            "description": "时间类型: 0=修改时间(默认), 1=创建时间, 2=线上下单时间, 3=发货时间",
        },
        "shop_id": {"type": "integer", "description": "店铺编号（对应 userId）"},
        "warehouse_id": {"type": "integer", "description": "订单分仓ID"},
        "start_date": {"type": "string", "description": "起始时间 yyyy-MM-dd HH:mm:ss"},
        "end_date": {"type": "string", "description": "结束时间 yyyy-MM-dd HH:mm:ss"},
        "types": {"type": "string", "description": "订单类型(逗号分隔): 0=普通, 7=合并, 8=拆分 等"},
        "refund_type": {
            "type": "integer",
            "description": "售后类型(仅refund_list): 1=退款, 2=退货, 3=补发, 4=换货, 5=发货前退款",
        },
        "refund_id": {"type": "integer", "description": "售后工单号(仅refund_list)"},
        "page_size": {"type": "integer", "description": "每页条数(默认20, 最小1)"},
    },
)
```

路由提示词更新：
```
"## 淘宝奇门查询\n"
"- 淘宝/天猫订单查询 → erp_taobao_query(action=order_list)\n"
"- 淘宝/天猫售后单 → erp_taobao_query(action=refund_list)\n"
"- 统计订单数量：用 page_size=1 只取 total\n"
"- 按店铺统计：先用 erp_info_query(action=shop_list) 获取店铺列表，再按 shop_id 逐个查 total\n"
```

### 7.6 格式化器（formatters/qimen.py）

```python
def format_qimen_order_list(data: Any, entry: ApiEntry) -> str:
    """淘宝订单列表（trades key）"""
    items = data.get("trades") or []
    total = data.get("total", 0)
    if not items:
        return "未找到符合条件的淘宝订单"
    lines = [f"共找到 {total} 条淘宝订单：\n"]
    for order in items[:20]:
        lines.append(_format_taobao_order(order))
    if total > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)


def format_qimen_refund_list(data: Any, entry: ApiEntry) -> str:
    """淘宝售后单列表（workOrders key）"""
    items = data.get("workOrders") or []
    total = data.get("total", 0)
    if not items:
        return "未找到符合条件的淘宝售后单"
    lines = [f"共找到 {total} 条售后单：\n"]
    for wo in items[:20]:
        lines.append(_format_taobao_refund(wo))
    if total > len(items):
        lines.append(f"\n（显示前{len(items)}条，共{total}条）")
    return "\n".join(lines)
```

`_format_taobao_order()` 复用现有字段映射（tid/sid/sysStatus/buyerNick/payment/shopName/created/payTime），额外展示：
- `type`: 订单类型
- `warehouseName`: 仓库
- `isPresell`: 预售标记
- `stockStatus`: 库存状态

`_format_taobao_refund()` 展示关键字段：
- `id`: 工单号
- `tid/sid`: 关联订单
- `afterSaleType`: 售后类型（1退款/2退货/3补发/4换货/5发货前退款）
- `status`: 工单状态（1未分配 ~ 10作废）
- `refundMoney`: 退款金额
- `shopName`: 店铺
- `reason`: 售后原因
- `items[]`: 售后商品明细

### 7.7 配置（config.py）

```python
# 快麦奇门接口配置
qimen_customer_id: Optional[str] = None       # 商家路由ID（授权时从快麦获取）
qimen_order_url: str = "http://33c367ryyg.api.taobao.com/router/qm"
qimen_refund_url: str = "http://z29932hpkn.api.taobao.com/router/qm"
qimen_target_app_key: str = "23204092"
```

> **注意**：奇门复用快麦 ERP 的 appKey/appSecret/accessToken，不需要额外的认证凭证。

---

## 8. API 设计

无新增 HTTP API。奇门接口通过 Agent 工具链调用（用户消息 → Agent Loop → ToolExecutor → ErpDispatcher → KuaiMaiClient → 淘宝网关）。

---

## 9. 开发任务拆分

### 阶段1：基础设施（3个任务，可并行）

- [ ] **任务1.1**：扩展 `ApiEntry`（base.py）— 新增 `base_url` + `system_params` 字段
- [ ] **任务1.2**：扩展 `KuaiMaiClient.request()`（client.py）— 支持 `base_url` 覆盖 + `extra_system_params`，同步扩展 `request_with_retry()`
- [ ] **任务1.3**：新增配置项（config.py + .env.example）— 添加 `qimen_*` 配置

### 阶段2：注册表 + 调度（2个任务，依赖阶段1）

- [ ] **任务2.1**：新建奇门注册表（registry/qimen.py）— 2个 ApiEntry（order_list / refund_list），导入到 `__init__.py` 并添加 TOOL_REGISTRIES 映射
- [ ] **任务2.2**：扩展调度器（dispatcher.py）— 检测 `entry.base_url` 时注入 `customerId`，传入 `base_url` + `extra_system_params`

### 阶段3：格式化 + 工具定义（2个任务，依赖阶段2）

- [ ] **任务3.1**：新建奇门格式化器（formatters/qimen.py）— `format_qimen_order_list` / `format_qimen_refund_list`，注册到 `formatters/__init__.py`
- [ ] **任务3.2**：新增工具定义（erp_tools.py）— `erp_taobao_query` 工具 + 路由提示词更新 + `ERP_SYNC_TOOLS` 集合新增

### 阶段4：测试

- [ ] **任务4.1**：单元测试 — 注册表、参数映射、格式化器、调度器
- [ ] **任务4.2**：集成验证 — 确认 Agent 大脑能正确选择 `erp_taobao_query` 工具

---

## 10. 依赖变更

无需新增依赖。

---

## 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 奇门签名与ERP签名算法不一致 | 中 | 淘宝奇门文档确认使用相同 HMAC 签名；首次测试时验证 |
| Token 刷新只走ERP网关，奇门是否共享 | 低 | 同一 appKey 的 Token 跨网关有效；万一不行，需增加奇门独立 Token |
| 订单/售后请求域名后续可能变更 | 低 | URL 放入 config.py 可随时覆盖 |
| customerId 获取流程不明 | 中 | 需用户在快麦授权时获取并配到 .env |
| AI 大脑在 ERP 工具和奇门工具之间选择混乱 | 中 | 路由提示词明确区分：ERP系统内部数据用 `erp_*`，淘宝平台数据用 `erp_taobao_query` |

---

## 12. 文档更新清单

- [ ] FUNCTION_INDEX.md — 新增奇门相关函数
- [ ] .env.example — 新增 QIMEN_* 配置

---

## 13. 设计自检

- [x] 连锁修改已全部纳入任务拆分
- [x] 7类边界场景均有处理策略
- [x] 所有新增文件预估≤500行（registry/qimen.py ~50行, formatters/qimen.py ~120行）
- [x] 无新增依赖
- [x] 向后兼容：`ApiEntry` 新增字段全有默认值，不影响现有 7 个 ERP 工具

## 技术设计：快麦ERP API对接

### 1. 现有代码分析

**已阅读文件**：
- `backend/core/config.py` — Pydantic Settings 配置管理，env vars 加载
- `backend/core/exceptions.py` — 异常层级（AppException → ExternalServiceError）
- `backend/config/agent_tools.py` — Agent 工具定义（9个工具，分 SYNC/ASYNC/TERMINAL）
- `backend/services/tool_executor.py` — 同步工具执行器（dispatch map 模式）
- `backend/services/dashscope_client.py` — httpx.AsyncClient 懒初始化模式
- `backend/services/adapters/` — 外部API适配器模式（lazy init + tenacity retry）
- `backend/.env.example` — 环境变量示例格式

**可复用模块**：
- `ExternalServiceError` 异常基类 → 派生 KuaiMai 异常
- `DashScopeClient` 的 httpx 懒初始化模式 → KuaiMai Client
- `ToolExecutor` dispatch map → 注册快麦工具
- `agent_tools.py` 工具注册表 → 添加ERP工具

**设计约束**：
- 必须兼容现有 Agent Loop 的 SYNC_TOOLS → 结果回传大脑迭代
- 工具参数必须注册到 TOOL_SCHEMAS 防幻觉
- 配置必须走 env vars，禁止硬编码

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| Settings 新增 kuaimai_* 字段 | `core/config.py` | 无影响（Optional 字段，向后兼容） |
| SYNC_TOOLS 新增 4 个工具名 | `config/agent_tools.py` | ALL_TOOLS 自动更新（集合运算） |
| TOOL_SCHEMAS 新增 4 个 schema | `config/agent_tools.py` | validate_tool_call 自动生效 |
| build_agent_tools() 新增 4 个定义 | `config/agent_tools.py` | AGENT_TOOLS 模块常量自动重建 |
| build_agent_system_prompt() 补充路由规则 | `config/agent_tools.py` | AGENT_SYSTEM_PROMPT 自动重建 |
| _handlers 新增 4 个工具 | `services/tool_executor.py` | execute() dispatch 自动生效 |
| .env.example 新增快麦配置 | `backend/.env.example` | 根目录 `.env.example` 同步 |

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 快麦配置未填（appKey/secret 为空） | 工具执行时返回友好提示"ERP未配置"，不抛异常 | tool_executor |
| accessToken 过期（30天） | 自动调用 `open.token.refresh` 刷新，成功后重试原请求 | kuaimai/client.py |
| Token 刷新失败 | 返回"ERP授权已过期，请联系管理员重新授权" | kuaimai/client.py |
| Token 刷新频率限制（1次/小时） | Redis 记录上次刷新时间，1小时内不重复刷新 | kuaimai/client.py |
| 签名错误（code=25） | 记录日志 + 返回明确错误信息 | kuaimai/client.py |
| 网络超时 | tenacity 3次重试，指数退避(1s/2s/4s) | kuaimai/client.py |
| 分页数据量大 | 单次最大100/200条，返回分页信息提示用户 | kuaimai/service.py |
| 查询无结果 | 返回友好文案"未找到符合条件的数据" | kuaimai/service.py |
| 快麦API返回业务错误 | 解析 code + msg，返回中文错误描述 | kuaimai/errors.py |
| 60天无调用权限被回收 | 捕获认证错误，提示重新申请 | kuaimai/client.py |
| 并发请求 | httpx.AsyncClient 连接池复用，无竞态问题 | kuaimai/client.py |

### 3. 技术栈

- 后端：Python 3.x + FastAPI（已有）
- HTTP 客户端：httpx（已有依赖）
- 重试：tenacity（已有依赖）
- 签名：hashlib + hmac（标准库）
- Token 缓存：Redis（已有）
- 日志：loguru（已有）
- **无需新增依赖**

### 4. 目录结构

#### 新增文件
```
backend/services/kuaimai/
├── __init__.py         # (~5行) 导出 KuaiMaiClient, KuaiMaiService
├── client.py           # (~200行) HTTP客户端：签名、请求、Token管理
├── service.py          # (~200行) 业务服务：订单/商品/库存/出库查询封装
└── errors.py           # (~40行) 自定义异常
```

#### 修改文件
- `backend/core/config.py` — 新增 kuaimai_* 配置字段（+15行）
- `backend/config/agent_tools.py` — 新增4个ERP工具定义 + 系统提示词更新（+100行）
- `backend/services/tool_executor.py` — 新增4个工具handler（+80行）
- `backend/.env.example` — 新增快麦配置段（+8行）

### 5. 数据库设计

**无需新增表**。

Token 管理策略：
- `kuaimai_app_key` / `kuaimai_app_secret`：环境变量（静态，不变）
- `kuaimai_access_token` / `kuaimai_refresh_token`：环境变量提供初始值
- 运行时 Token 刷新后：写入 Redis `kuaimai:access_token` (TTL=29天)
- 启动时优先读 Redis，Redis 无值则回退 env var

### 6. API设计

#### 快麦不需要暴露前端API

快麦数据通过 **Agent 工具** 提供给 AI，由 AI 在对话中自动调用。不直接暴露 REST 端点给前端。

#### 内部工具接口（Agent 调用）

**工具1：query_erp_orders**
- 类型：SYNC_TOOL（结果回传大脑）
- 请求参数：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| query_type | string | 是 | 查询方式：by_order_id / by_time_range / by_status |
| order_id | string | 否 | 平台订单号（query_type=by_order_id 时必填） |
| start_date | string | 否 | 起始日期 yyyy-MM-dd（query_type=by_time_range 时必填） |
| end_date | string | 否 | 结束日期 yyyy-MM-dd |
| status | string | 否 | 订单状态筛选 |
| page | integer | 否 | 页码，默认1 |

- 调用快麦接口：`erp.trade.list.query`
- 返回格式：格式化文本（给大脑阅读）

**工具2：query_erp_products**
- 类型：SYNC_TOOL
- 请求参数：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| query_type | string | 是 | 查询方式：by_code / by_name / list_all |
| product_code | string | 否 | 商家编码（query_type=by_code 时） |
| keyword | string | 否 | 商品名称关键词（query_type=by_name 时） |
| page | integer | 否 | 页码，默认1 |

- 调用快麦接口：`item.single.get` / `item.list.query`

**工具3：query_erp_inventory**
- 类型：SYNC_TOOL
- 请求参数：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| product_code | string | 否 | 商家编码（精确查询） |
| stock_status | string | 否 | 库存状态：normal/warning/out_of_stock/oversold |
| warehouse_id | string | 否 | 仓库ID |
| page | integer | 否 | 页码，默认1 |

- 调用快麦接口：`stock.api.status.query`

**工具4：query_erp_shipment**
- 类型：SYNC_TOOL
- 请求参数：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| query_type | string | 是 | 查询方式：by_order_id / by_waybill / by_time_range |
| order_id | string | 否 | 订单号 |
| waybill_no | string | 否 | 快递单号 |
| start_date | string | 否 | 起始日期 |
| end_date | string | 否 | 结束日期 |
| page | integer | 否 | 页码，默认1 |

- 调用快麦接口：`erp.trade.outstock.simple.query`

### 7. 核心类设计

#### 7.1 KuaiMaiClient（HTTP 客户端）

```
class KuaiMaiClient:
    """快麦ERP API 客户端

    职责：签名计算、HTTP 请求、Token 自动刷新
    """

    属性:
        _app_key: str
        _app_secret: str
        _access_token: str
        _refresh_token: str
        _client: Optional[httpx.AsyncClient]  # 懒初始化
        _redis: Optional[Redis]               # Token 缓存

    方法:
        generate_sign(params, sign_method="hmac") -> str
            # 签名算法：参数排序→拼接→HMAC_MD5→32位大写HEX

        request(method: str, biz_params: dict) -> dict
            # 发送API请求（自动签名、自动Token刷新）
            # @retry(3次, 指数退避, 仅网络/超时错误)

        refresh_token() -> None
            # 调用 open.token.refresh 刷新 Token
            # Redis 限流：1次/小时

        close() -> None
            # 关闭 httpx 客户端
```

#### 7.2 KuaiMaiService（业务服务）

```
class KuaiMaiService:
    """快麦ERP 业务查询服务

    职责：封装业务查询逻辑，格式化返回结果为大脑可读文本
    """

    属性:
        _client: KuaiMaiClient

    方法:
        query_orders(query_type, order_id, start_date, ...) -> str
            # 订单查询 → 格式化文本

        query_products(query_type, product_code, keyword, ...) -> str
            # 商品查询 → 格式化文本

        query_inventory(product_code, stock_status, ...) -> str
            # 库存查询 → 格式化文本

        query_shipment(query_type, order_id, waybill_no, ...) -> str
            # 出库/物流查询 → 格式化文本

        _format_order(order: dict) -> str       # 格式化单个订单
        _format_product(product: dict) -> str   # 格式化单个商品
        _format_inventory(item: dict) -> str    # 格式化库存行
        _format_shipment(item: dict) -> str     # 格式化出库行
```

#### 7.3 异常体系

```
KuaiMaiError(ExternalServiceError)           # 基类
├── KuaiMaiSignatureError(KuaiMaiError)      # 签名错误 (code=25)
├── KuaiMaiTokenExpiredError(KuaiMaiError)   # Token过期
├── KuaiMaiRateLimitError(KuaiMaiError)      # 频率限制
└── KuaiMaiBusinessError(KuaiMaiError)       # 其他业务错误
```

### 8. Agent 系统提示词更新

在 `build_agent_system_prompt()` 的路由规则中新增：

```
## ERP数据查询规则
- 用户问订单状态/物流/发货 → query_erp_orders 或 query_erp_shipment
- 用户问库存/缺货/补货 → query_erp_inventory
- 用户问商品信息/编码/价格 → query_erp_products
- ERP查询结果返回后，你可以继续用 text_chat 总结回复用户
- 如果ERP未配置，直接告知用户需要配置快麦ERP
```

### 9. 开发任务拆分

#### 阶段1：基础设施（无依赖）
- [ ] 任务1.1：`core/config.py` 新增 kuaimai_* 配置字段（4个）
- [ ] 任务1.2：`backend/.env.example` 新增快麦配置段
- [ ] 任务1.3：`services/kuaimai/errors.py` 异常定义

#### 阶段2：核心客户端（依赖阶段1）
- [ ] 任务2.1：`services/kuaimai/client.py` — 签名算法 + HTTP请求
- [ ] 任务2.2：`services/kuaimai/client.py` — Token自动刷新 + Redis缓存
- [ ] 任务2.3：`services/kuaimai/__init__.py` 导出

#### 阶段3：业务服务（依赖阶段2）
- [ ] 任务3.1：`services/kuaimai/service.py` — 订单查询 + 格式化
- [ ] 任务3.2：`services/kuaimai/service.py` — 商品查询 + 库存查询
- [ ] 任务3.3：`services/kuaimai/service.py` — 出库/物流查询

#### 阶段4：Agent 工具注册（依赖阶段3）
- [ ] 任务4.1：`config/agent_tools.py` — 新增4个工具到 SYNC_TOOLS + TOOL_SCHEMAS
- [ ] 任务4.2：`config/agent_tools.py` — build_agent_tools() 新增4个工具定义
- [ ] 任务4.3：`config/agent_tools.py` — build_agent_system_prompt() 更新路由规则
- [ ] 任务4.4：`services/tool_executor.py` — 新增4个工具handler

#### 阶段5：测试（依赖阶段4）
- [ ] 任务5.1：签名算法单元测试
- [ ] 任务5.2：Client 请求 + Token刷新单元测试（mock httpx）
- [ ] 任务5.3：Service 业务查询单元测试（mock client）
- [ ] 任务5.4：工具注册验证测试
- [ ] 任务5.5：端到端集成测试（需真实 appKey）

### 10. 依赖变更

**无需新增依赖**。所有所需库已存在：
- `httpx` — HTTP 客户端
- `tenacity` — 重试机制
- `hashlib` / `hmac` — Python 标准库
- `redis` (via `core/redis.py`) — Token 缓存
- `loguru` — 日志

### 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| Token 30天过期未刷新 | 高 | 自动刷新 + Redis持久化 + 过期告警日志 |
| 签名算法实现有误 | 中 | 对照官方Python示例 + 单元测试覆盖 |
| 快麦API响应慢 | 中 | httpx timeout 10s + Agent Loop 有总超时 |
| Agent 工具过多导致路由不准 | 中 | 工具描述清晰 + 观察后可合并 |
| 快麦API变更/下线 | 低 | 错误日志告警 + 降级友好提示 |
| 生产环境 Redis 重启丢Token | 低 | 启动时从 env var 兜底 |

### 12. 环境变量配置

```env
# 快麦ERP 配置
KUAIMAI_APP_KEY=your-kuaimai-app-key
KUAIMAI_APP_SECRET=your-kuaimai-app-secret
KUAIMAI_ACCESS_TOKEN=your-access-token
KUAIMAI_REFRESH_TOKEN=your-refresh-token
```

### 13. 文档更新清单
- [ ] FUNCTION_INDEX.md — 新增快麦相关函数
- [ ] PROJECT_OVERVIEW.md — 新增 kuaimai/ 目录说明
- [ ] .env.example（根目录 + backend/）

### 14. 设计自检
- [x] 连锁修改已全部纳入任务拆分（config.py、agent_tools.py、tool_executor.py、.env.example）
- [x] 7类边界场景均有处理策略（见第2节）
- [x] 所有新增文件预估≤500行（client.py ~200行、service.py ~200行、errors.py ~40行）
- [x] 无模糊版本号依赖（无新增依赖）
- [x] 遵循现有代码模式（httpx懒初始化、tenacity重试、loguru日志、SYNC_TOOLS注册）

---
**确认后进入开发阶段**

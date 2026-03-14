## 技术设计：用户定位（IP 地理位置注入）

### 1. 现有代码分析

**已阅读文件**：
- `backend/main.py`（390行）— SecurityHeadersMiddleware 第116行 `Permissions-Policy: geolocation=()` 主动禁用浏览器定位
- `backend/api/routes/message.py`（471行）— `generate_message()` 统一消息入口，接收 `Request` 对象
- `backend/services/intent_router.py`（578行）— 路由决策 + `execute_search()` 搜索执行
- `backend/services/handlers/chat_context_mixin.py`（489行）— `_build_llm_messages()` 组装 LLM 消息列表，第100行已注入当前时间
- `backend/services/agent_context.py`（60行）— Agent Loop 上下文构建
- `backend/core/config.py`（230行）— 应用配置
- `backend/api/deps.py`（119行）— 依赖注入
- `backend/schemas/message.py` — GenerateRequest 请求模型
- `deploy/nginx.conf` — 第101-102行已设置 `X-Real-IP` / `X-Forwarded-For` 头
- `frontend/src/services/messageSender.ts`（458行）— 前端消息发送
- `frontend/src/utils/settingsStorage.ts`（193行）— 用户本地设置

**可复用模块**：
- `chat_context_mixin.py:100` — 已有时间注入模式（`当前时间：xxx`），位置信息用相同模式注入
- `nginx.conf:101-102` — 已配置 `X-Real-IP` / `X-Forwarded-For`，后端直接从 Request 头提取 IP
- `config.py` — Pydantic Settings 模式，新增配置项即可

**设计约束**：
- 不改变前端消息发送流程（纯后端方案，前端零改动）
- 不改变 GenerateRequest schema（不增加字段）
- IP 定位是辅助信息，失败静默降级，不影响主流程
- 遵循 ChatGPT 模式：后端 IP 定位 → 注入 system prompt

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增 `_user_location` 到 params | `api/routes/message.py` | `generate_message()` 中提取 IP 并注入 params |
| `_build_llm_messages()` 新增 location 参数 | `chat_context_mixin.py` | 注入位置 system prompt |
| Agent Loop 上下文注入位置 | `services/agent_context.py` | `_build_system_messages()` 注入位置 |
| 新增 ip_location_service.py | 无连锁 | 独立模块 |
| config.py 新增配置项 | `.env` / `.env.production` | 补充高德 API Key |

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| IP 定位 API 超时/不可用 | 静默跳过，不注入位置（catch + warning 日志）| ip_location_service |
| IP 解析失败（内网IP/VPN/代理） | 返回 None，不注入位置 | ip_location_service |
| 高德 API Key 未配置 | 功能自动禁用，日志提示 | config.py + ip_location_service |
| 高德免费额度耗尽 | Redis 缓存 IP→城市映射，减少调用量 | ip_location_service |
| 并发请求同一 IP | Redis 缓存命中，不重复调 API | ip_location_service |
| 用户使用 VPN（IP 漂移） | 接受误差，位置仅为辅助参考 | 无需特殊处理 |
| IPv6 地址 | 高德 API 支持 IPv6 查询 | ip_location_service |
| 企微/Webhook 渠道（无真实用户 IP） | 跳过定位（无 Request 对象） | message.py 条件判断 |

### 3. 技术栈

- 前端：**无改动**
- 后端：Python 3.x + FastAPI + httpx（已有）
- 缓存：Redis（已有）
- 外部服务：高德 IP 定位 API（免费 30万次/天）
- 数据库：**无改动**（不新增表）

### 4. 目录结构

#### 新增文件
- `backend/services/ip_location_service.py`：IP 地理位置解析服务（高德 API + Redis 缓存）

#### 修改文件
- `backend/core/config.py`：新增高德 API 配置项
- `backend/api/routes/message.py`：提取用户 IP → 调用定位服务 → 注入 params
- `backend/services/handlers/chat_context_mixin.py`：`_build_llm_messages()` 注入位置 system prompt
- `backend/services/agent_context.py`：Agent Loop 系统提示词注入位置
- `deploy/.env.production`：新增 `AMAP_API_KEY`

### 5. 数据库设计

**无新增表。** IP→城市映射通过 Redis 缓存（TTL 24h），不持久化。

Redis 缓存格式：
```
Key:   ip_loc:{ip_address}
Value: {"city": "深圳", "province": "广东", "country": "中国"}
TTL:   86400 (24小时)
```

### 6. API 设计

#### 外部 API：高德 IP 定位

```
GET https://restapi.amap.com/v3/ip?ip={ip}&key={amap_key}
```

成功响应：
```json
{
  "status": "1",
  "province": "广东省",
  "city": "深圳市",
  "adcode": "440300",
  "rectangle": "113.75,22.45;114.62,22.84"
}
```

#### 内部接口（无新增 HTTP API）

定位结果通过 `params["_user_location"]` 在内部传递：
```python
# 注入到 params 的格式
params["_user_location"] = "广东省深圳市"  # 或 None
```

### 7. 核心实现设计

#### 7.1 ip_location_service.py（新增，约80行）

```python
# 接口定义（不含实现）
async def get_location_by_ip(ip: str) -> Optional[str]:
    """IP → 城市名（如"广东省深圳市"），失败返回 None

    流程：Redis 缓存 → 高德 API → 缓存结果
    内网/保留 IP 直接返回 None
    """

def extract_client_ip(request: Request) -> str:
    """从 FastAPI Request 提取真实客户端 IP

    优先级：X-Real-IP → X-Forwarded-For 首个 → request.client.host
    """
```

#### 7.2 chat_context_mixin.py 改动

在 `_build_llm_messages()` 第100行（当前时间注入后）增加位置注入：

```python
# 当前日期时间注入
now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
messages.insert(0, {"role": "system", "content": f"当前时间：{now_str}"})

# 用户位置注入（新增，紧跟时间注入）
if user_location:
    messages.insert(0, {"role": "system", "content": f"用户所在位置：{user_location}"})
```

#### 7.3 message.py 改动

在 `generate_message()` 中提取 IP 并异步获取位置，注入 params：

```python
# 在路由决策之前（不阻塞主流程，与路由并行）
from services.ip_location_service import extract_client_ip, get_location_by_ip

client_ip = extract_client_ip(request)
# 与路由决策并行执行
location_task = asyncio.create_task(get_location_by_ip(client_ip))

# ... 路由决策完成后 ...
user_location = await location_task  # 有缓存时几乎 0 延迟
if user_location:
    body.params["_user_location"] = user_location
```

#### 7.4 agent_context.py 改动

Agent Loop 系统提示词中注入位置信息（与 chat_context_mixin 相同模式）。

### 8. 开发任务拆分

#### 阶段1：后端核心（独立，无前端依赖）

- [ ] 任务1.1：`config.py` 新增高德 API 配置项（`amap_api_key`、`ip_location_timeout`、`ip_location_cache_ttl`）
- [ ] 任务1.2：新建 `ip_location_service.py`（IP 提取 + 高德 API 调用 + Redis 缓存）
- [ ] 任务1.3：`message.py` `generate_message()` 提取 IP → 异步获取位置 → 注入 params
- [ ] 任务1.4：`chat_context_mixin.py` `_build_llm_messages()` 接收并注入位置 system prompt
- [ ] 任务1.5：`agent_context.py` Agent Loop 系统提示词注入位置

#### 阶段2：配置与部署

- [ ] 任务2.1：`.env.production` 新增 `AMAP_API_KEY`
- [ ] 任务2.2：`.env.example` 同步更新

#### 阶段3：测试验证

- [ ] 任务3.1：`ip_location_service.py` 单元测试（mock 高德 API + Redis）
- [ ] 任务3.2：端到端验证（发送"今天天气怎么样"验证位置注入效果）

### 9. 依赖变更

**无需新增依赖。** 使用已有的：
- `httpx`（已有）— 调用高德 API
- `redis`（已有，通过 `core.redis.RedisClient`）— 缓存 IP→城市

### 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 高德 API 不可用 | 低 | 静默降级（不注入位置），不影响主流程 |
| IP 定位不准（VPN/代理） | 低 | 位置仅辅助参考，模型仍可正常回答 |
| Redis 缓存不可用 | 低 | 跳过缓存直接调 API，降级不中断 |
| 增加请求延迟 | 低 | 与路由决策并行执行 + Redis 缓存命中后 <1ms |
| 高德免费额度不足 | 极低 | 30万次/天，Redis 24h 缓存后实际调用量极低 |

### 11. 文档更新清单

- [ ] FUNCTION_INDEX.md — 新增 `ip_location_service.py`
- [ ] PROJECT_OVERVIEW.md — 更新架构说明

### 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（5处改动 → 5个任务）
- [x] 7类边界场景均有处理策略（见第2节，8个场景）
- [x] 所有新增文件预估 ≤500行（ip_location_service.py 约80行）
- [x] 无模糊版本号依赖（无新增依赖）
- [x] 前端零改动，纯后端方案
- [x] 失败静默降级，不影响任何现有功能

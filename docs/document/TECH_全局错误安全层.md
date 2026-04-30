# 技术设计：全局错误安全层

> **等级**：A 级 | **日期**：2026-04-30
> **前置**：方案评审已完成（4 角色 3 轮辩论，共识已形成）

---

## 1. 项目上下文

### 架构现状
项目已有完善的三层错误处理体系：
- **异常层**（`core/exceptions.py` 198 行）：13 个 `AppException` 子类，覆盖认证/权限/资源/业务/外部服务
- **分类层**（`core/error_classifier.py`）：任意异常 → `ClassifiedError`（5 类：MODEL/INFRA/BUSINESS/TRANSIENT/UNKNOWN）
- **监控层**（`core/error_alert_sink.py`）：loguru sink → 异步队列 → DB 持久化 → 企微告警

全局 handler 4 个：`RequestValidationError(422)` / `AppException(动态)` / `RowNotFoundError(404)` / `Exception(500)`

### 可复用模块
- `AppException` 层级 + `main.py` 的 `app_exception_handler` —— 新增异常类自动走这个 handler，无需注册新 handler
- `QueryResponse` dataclass —— 新增 `first` 属性即可
- `ExternalServiceError(503)` —— 外部服务错误已有，只需新增 `ConfigurationError(500)` 补缺口

### 设计约束
- 响应格式必须保持 `{error: {code, message, details}}`，前端已有 `err.response?.data?.error?.message` 提取逻辑
- `AppException` 子类自动被 `app_exception_handler` 捕获，无需额外注册
- `RowNotFoundError` 不继承 `AppException`（它在 `local_db.py` 中，独立 handler）

### 潜在冲突
- 无。所有修改都是把裸 `ValueError` 替换为已有异常类的子类，不改调用链

---

## 2. 代码分析

### 已阅读文件
| 文件 | 行数 | 关键理解 |
|------|------|---------|
| `core/exceptions.py` | 198 | 13 个异常类，缺 ConfigurationError |
| `core/local_db.py` | 711 | QueryResponse 只有 data/count 两个字段，无安全访问属性 |
| `main.py` | 633 | 4 个全局 handler，AppException 子类自动走动态状态码 |
| `adapters/factory.py` | ~800 | 9 处 `raise ValueError` 用于 API Key 缺失和 Provider 不支持 |
| `wecom_oauth_service.py` | ~500 | 11 处 `raise ValueError` 混杂了验证/权限/外部服务三种语义 |
| `storage_service.py` | ~220 | 7 处 `raise ValueError` 用于文件类型/大小校验和上传失败 |
| `kie/client.py` | ~400 | 2 处 `.json()` 无 try-except 保护 |

### 审计修正（关键发现）
- **`.data[0]` 在 API 路由层全部已有边界检查**，0 处裸访问。`first` 属性仍然有价值（services 层有裸访问 + 防御未来新代码），但不是紧急修复项
- 实际需修复：**9 + 11 + 16 + 2 = 38 处**（factory + wecom_oauth + validation + json）

---

## 3. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| API Key 全部未配置 | `ConfigurationError(500)` → "该功能暂未开通" | factory.py |
| 企微 API 返回非 JSON | `ExternalServiceError(503)` 包装 JSONDecodeError | kie/client.py |
| Redis 不可用时 OAuth | `ExternalServiceError(503)` → "服务暂时繁忙" | wecom_oauth |
| 用户上传超大文件 | `ValidationError(400)` → 现有中文提示保留 | storage_service |
| 并发请求同一个缺失 Key | 每次都返回 500，无缓存——正确行为（配置修复前应持续报错） | factory.py |
| 前端收到新状态码 | 无影响——前端已用 `error?.message` 提取，不依赖状态码 | 前端 |

---

## 4. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增 `ConfigurationError` 类 | `core/exceptions.py` | 无——AppException 子类自动被全局 handler 捕获 |
| `QueryResponse.first` 属性 | `core/local_db.py` | 无——新增属性，不影响现有 `.data` 用法 |
| factory.py ValueError → ConfigurationError | `services/adapters/factory.py` | 新增 `from core.exceptions import ConfigurationError` |
| wecom_oauth ValueError → 多种异常 | `services/wecom_oauth_service.py` | 新增多个 import |
| storage/audio ValueError → ValidationError | 2 个文件 | 新增 import |
| cron_utils ValueError → ValidationError | 1 个文件 | 新增 import |
| kie/client.py .json() 保护 | 1 个文件 | 无新增依赖 |
| 测试更新 | 各 test 文件 | `pytest.raises(ValueError)` → `pytest.raises(对应异常类)` |

---

## 5. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|---------|---------|
| 模块边界 | 不跨模块，只在各文件内替换异常类 | 低 | 无 |
| 数据流向 | 不变——异常仍沿调用栈冒泡到全局 handler | 低 | 无 |
| 扩展性 | `ConfigurationError` 可被未来任何 Provider 复用 | 低 | 无 |
| 耦合度 | 新增 1 个异常类，被 factory.py 引用 | 低 | 异常类在 core 层，合理 |
| 一致性 | 与现有 `ExternalServiceError`/`ValidationError` 模式完全一致 | 低 | 无 |
| 可观测性 | ConfigurationError(500) → Sentry 告警；ValidationError(400) → warning 日志 | 低 | 日志分级已由全局 handler 处理 |
| 可回滚性 | 纯代码替换，无 DB 迁移，git revert 即可 | 低 | 无 |

---

## 6. 方案对比

评审阶段已完成方案对比（全局拦截原生异常 vs 逐点分类替换），共识：**逐点分类替换**。不再重复。

---

## 7. 技术栈
无新增依赖，沿用现有：Python 3.11 + FastAPI + PostgreSQL + Redis

---

## 8. 修改文件清单

### 新增：无

### 修改文件

| 文件 | 改动内容 | 预估行数变化 |
|------|---------|-------------|
| `core/exceptions.py` | 新增 `ConfigurationError` 类 | +15 |
| `core/local_db.py` | `QueryResponse` 新增 `first` 属性 | +8 |
| `services/adapters/factory.py` | 9 处 ValueError → ConfigurationError | ±0（替换） |
| `services/wecom_oauth_service.py` | 11 处 ValueError → 分类替换 | ±0（替换） |
| `services/storage_service.py` | 7 处 ValueError → ValidationError / ExternalServiceError | ±0 |
| `services/audio_service.py` | 4 处 ValueError → ValidationError | ±0 |
| `services/scheduler/cron_utils.py` | 9 处 ValueError → ValidationError | ±0 |
| `services/adapters/kie/client.py` | 2 处 `.json()` 加 try-except | +10 |

---

## 9. 数据库设计
无数据库变更。

---

## 10. API 设计
无新增 API。响应格式不变，状态码从 500 变为对应的 400/403/404/500/503。

---

## 11. 详细修复清单

### Phase 0：基础设施（`exceptions.py` + `local_db.py`）

**ConfigurationError 定义**：
```python
class ConfigurationError(AppException):
    """服务端配置缺失（API Key 未配、Provider 未实现）"""
    def __init__(self, service: str, message: str = "该功能暂未开通，请联系管理员"):
        super().__init__(
            code="SERVICE_NOT_CONFIGURED",
            message=message,
            status_code=500,
            details={"service": service},
        )
```

**QueryResponse.first 属性**：
```python
@property
def first(self) -> dict | None:
    if isinstance(self.data, list):
        return self.data[0] if self.data else None
    return self.data  # single()/maybe_single() 返回 dict 或 None
```

### Phase 1：factory.py（9 处 → ConfigurationError）

| 行 | 原代码 | 替换为 |
|----|--------|--------|
| 551 | `raise ValueError("KIE API Key 未配置")` | `raise ConfigurationError("KIE")` |
| 561 | `raise ValueError("DashScope API Key 未配置")` | `raise ConfigurationError("DashScope")` |
| 575 | `raise ValueError("OpenRouter API Key 未配置")` | `raise ConfigurationError("OpenRouter")` |
| 590 | `raise ValueError("Google API Key 未配置")` | `raise ConfigurationError("Google")` |
| 598 | `raise ValueError(f"Provider {config.provider} 暂未实现")` | `raise ConfigurationError(str(config.provider), f"模型供应商 {config.provider} 暂未支持")` |
| 668 | `raise ValueError("KIE API Key 未配置")` | `raise ConfigurationError("KIE")` |
| 674 | `raise ValueError(f"图片 Provider ... 暂未实现")` | `raise ConfigurationError(...)` |
| 736 | `raise ValueError("KIE API Key 未配置")` | `raise ConfigurationError("KIE")` |
| 742 | `raise ValueError(f"视频 Provider ... 暂未实现")` | `raise ConfigurationError(...)` |

### Phase 2：wecom_oauth_service.py（11 处 → 分类替换）

| 行 | 原 message | 替换异常类 | 理由 |
|----|-----------|-----------|------|
| 94 | "Redis 不可用" | `ExternalServiceError("Redis", "登录服务暂时不可用")` | 基础设施故障 |
| 99 | "state 无效或已过期" | `ValidationError("登录链接已失效，请重新扫码")` | 用户操作过期 |
| 138 | "获取企微 access_token 失败" | `ExternalServiceError("企微", "企业微信服务暂时不可用")` | 外部 API 故障 |
| 149 | "企微 API 调用失败" | `ExternalServiceError("企微", "企业微信服务暂时不可用")` | 外部 API 故障 |
| 155 | "企微授权失败（{errmsg}）" | `ExternalServiceError("企微", "企业微信授权失败，请重试")` | 外部 API 错误（不暴露 errmsg） |
| 161 | "仅限企业成员使用扫码登录" | `PermissionDeniedError("仅限企业成员使用扫码登录")` | 权限不足 |
| 283 | "用户账号异常，请联系管理员" | `ValidationError("用户账号异常，请联系管理员")` | 数据异常 |
| 287 | "账号已被禁用" | `PermissionDeniedError("账号已被禁用")` | 权限不足 |
| 410 | "该账号已绑定其他企微用户" | `ConflictError("该账号已绑定其他企微用户，请先解绑")` | 资源冲突 |
| 472 | "当前账号未绑定企微" | `ValidationError("当前账号未绑定企微")` | 前置条件不满足 |
| 483 | "该账号仅通过企微创建..." | `ValidationError("该账号仅通过企微创建，解绑后将无法登录，请先绑定手机号")` | 前置条件不满足 |

### Phase 3：验证类 ValueError（20 处 → ValidationError）

**storage_service.py**（7 处）：
- 文件类型/大小校验 → `ValidationError`（保留现有中文提示）
- 上传失败 → `ExternalServiceError("OSS", "文件上传失败，请重试")`

**audio_service.py**（4 处）：
- 文件类型/大小/URL 校验 → `ValidationError`（保留现有中文提示）

**cron_utils.py**（9 处）：
- 时间格式/范围/参数校验 → `ValidationError`（保留现有中文提示）

### Phase 4：kie/client.py .json() 保护（2 处）

```python
# 行 350 和 385：原代码
response_data = response.json()

# 替换为
try:
    response_data = response.json()
except Exception:
    raise KieAPIError(f"KIE API 返回非 JSON 响应: status={response.status_code}")
```

---

## 12. 开发任务拆分

### Phase 0：基础设施（预估 10 分钟）
- [ ] 0.1 `exceptions.py` 新增 `ConfigurationError`
- [ ] 0.2 `local_db.py` `QueryResponse` 新增 `first` 属性
- [ ] 0.3 补充单元测试

### Phase 1：factory.py 配置错误（预估 10 分钟）
- [ ] 1.1 9 处 ValueError → ConfigurationError
- [ ] 1.2 更新相关测试

### Phase 2：wecom_oauth 异常分类（预估 15 分钟）
- [ ] 2.1 11 处 ValueError → 5 种异常类分类替换
- [ ] 2.2 更新相关测试

### Phase 3：验证类 + JSON 保护（预估 15 分钟）
- [ ] 3.1 storage_service 7 处
- [ ] 3.2 audio_service 4 处
- [ ] 3.3 cron_utils 9 处
- [ ] 3.4 kie/client.py 2 处 .json() 保护
- [ ] 3.5 更新相关测试

### Phase 4：全量测试 + 部署
- [ ] 4.1 全量跑测试 6175+
- [ ] 4.2 部署到生产

---

## 13. 依赖变更
无新增依赖。

---

## 14. 部署与回滚策略
- **数据库迁移**：无
- **API 兼容**：完全向后兼容——响应格式不变，只是状态码从 500 变为更精确的 400/403/500/503
- **回滚步骤**：`git revert` 单个 commit 即可

---

## 15. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| 修改 wecom_oauth 的异常类导致前端处理分支变化 | 低 | 前端只读 `error.message`，不依赖状态码分支 |
| ConfigurationError(500) 与 INTERNAL_ERROR(500) 状态码相同 | 低 | code 字段不同（SERVICE_NOT_CONFIGURED vs INTERNAL_ERROR），可区分 |
| 测试中 `pytest.raises(ValueError)` 需同步更新 | 低 | 每个 Phase 同步更新测试 |

---

## 16. 文档更新清单
- [ ] FUNCTION_INDEX.md（新增 ConfigurationError）
- [ ] PROJECT_OVERVIEW.md（无文件增删，跳过）

---

## 17. 设计自检
- [x] 项目上下文已加载，4 点完整
- [x] 连锁修改已全部纳入任务拆分
- [x] 边界场景均有处理策略
- [x] 架构影响评估全低风险
- [x] 所有修改文件预估 ≤500 行
- [x] 无新增依赖

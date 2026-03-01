# 当前问题 (CURRENT_ISSUES)

> 本文档记录项目中当前存在的已知问题、待修复的Bug、技术债务等。

## 问题状态

**🔴 严重** | **🟡 中等** | **🟢 轻微** | **技术债务**：均无

---

## 会话交接记录

---

### 2026-02-01 聊天系统综合重构（阶段0-7完成，97%进度）

**关联文档**：[重构执行清单](docs/document/重构执行清单.md)

**完成内容摘要**：
- 阶段0：短期修复（9个任务）- 缓存写入、去重逻辑、模型切换、日志统一
- 阶段1：统一缓存写入（3个任务）- RuntimeStore 改用、兼容层
- 阶段2：合并发送器处理器（5/6任务）- mediaSender、useMediaMessageHandler
- 阶段3：统一轮询管理器（2个任务）- polling.ts 精简
- 阶段4：提取任务通知逻辑（2个任务）- taskNotification.ts

---

### 2026-02-02 阶段5-7：状态管理与性能优化（完成）

**阶段5 - 状态管理重设计**：
- 新建 `messageCoordinator.ts` 协调层，解耦 TaskStore 和 ChatStore
- 统一 `updateMessageId` 和 `markConversationUnread` 调用

**阶段6 - 占位符持久化**：
- tasks 表新增 `placeholder_created_at` 字段
- 页面刷新后任务恢复使用原始时间戳

**阶段7 - 性能优化**：
- 虚拟滚动（react-virtuoso）- 只渲染可见区域消息
- 消息合并算法 O(n²) → O(n)
- 图片加载失败指数退避重试

---

---

### 2026-03-01 刷新恢复场景僵尸消息修复（已解决）

**问题现象**：图片/视频生成任务 KIE 正常返回，但刷新页面后任务恢复时出现"僵尸消息"——占位符永远转圈、图片无法显示、出现多个"生成完成"文字气泡。

**根因分析**（3 个 Bug）：

| Bug | 严重度 | 根因 | 修复 |
|-----|--------|------|------|
| generation_params 类型不匹配 | 🔴 CRITICAL | Supabase JSONB 返回字符串，Pydantic `MessageResponse` 期望 dict → GET /messages 422 → 消息历史无法加载 | 添加 `field_validator` 自动 `json.loads` |
| 恢复订阅 ID 不匹配 | 🔴 HIGH | `taskRestoration.ts` 用 `external_task_id` 订阅 WS，但后端用 `client_task_id` 推送 → WS 订阅无法匹配 | `/tasks/pending` API 增加 `client_task_id` 返回，前端优先用 `client_task_id` 订阅 |
| 生产环境 debug print | 🟡 MEDIUM | 3 处 `print(f"🔥🔥🔥 ...")` 遗留在生产代码 | 删除 |

**修改文件**：
- `backend/schemas/message.py` — `field_validator('generation_params')` 自动转换
- `backend/api/routes/task.py` — select 增加 `client_task_id`
- `frontend/src/utils/taskRestoration.ts` — `PendingTask` 增加 `client_task_id`，订阅优先使用
- `backend/services/task_completion_service.py` — 删除 debug print

---

## 更新记录

- **2026-03-01**：修复刷新恢复场景僵尸消息（generation_params 类型 + WS 订阅 ID 不匹配 + debug print 清理）
- **2026-02-02**：完成阶段5-7（状态管理重设计、占位符持久化、性能优化）
- **2026-02-01**：完成聊天系统综合重构阶段0-4（缓存统一、发送器合并、轮询管理）
- **2026-01-31**：完成登录/注册弹窗化重构、消息重复修复、图片上传优化

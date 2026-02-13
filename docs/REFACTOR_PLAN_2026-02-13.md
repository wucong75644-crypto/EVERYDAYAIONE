# 代码重构执行计划（2026-02-13）

> **状态**：待执行
> **备份分支**：`backup/before-refactor-2026-02-13`
> **执行原则**：每个阶段独立测试，确保不引入新 bug

---

## 📋 重构目标

解决代码规范审查中发现的 **10 项违规**，按风险等级分阶段执行。

---

## 🎯 总体策略

### 核心原则
1. **小步快跑**：每次只改一个模块，立即测试
2. **向后兼容**：公开 API 接口不变，只重构内部实现
3. **测试先行**：先写测试，再重构，确保覆盖率 ≥80%
4. **独立分支**：每个 Phase 创建独立分支，测试通过后合并
5. **可回滚**：出问题立即 `git revert`，不强推

### 风险控制
- **每个 Phase 结束后**：运行测试 + 手动回归测试
- **合并前检查清单**：见附录 A
- **回滚预案**：见附录 B

---

## 📅 执行时间线（预计 5-7 天）

| Phase | 时间 | 风险等级 | 是否阻塞 |
|-------|------|---------|---------|
| Phase 0 | 0.5天 | 无 | 否 |
| Phase 1 | 1-2天 | 低 | **是**（后续依赖） |
| Phase 2 | 0.5天 | 低 | 否 |
| Phase 3 | 1-2天 | 🔴 极高 | **是**（核心模块） |
| Phase 4 | 1天 | 🟠 高 | 否 |
| Phase 5 | 1天 | 🟠 高 | 否 |
| Phase 6 | 0.5天 | 🟡 中 | 否 |
| Phase 7 | 0.5天 | 低 | 否 |

---

## Phase 0：准备阶段（0.5天）

### 目标
- [x] 创建备份分支 `backup/before-refactor-2026-02-13`
- [x] 推送到远程仓库
- [ ] 创建重构计划文档（本文档）
- [ ] 确认测试环境可用

### 验收标准
- [ ] 备份分支已推送
- [ ] 本文档已提交到 `docs/`
- [ ] `pytest` 和 `npm test` 可正常运行

---

## Phase 1：补充测试（1-2天）⚠️ 阻塞后续

### 目标
为高风险重构模块编写单元测试，建立安全网。

### 测试范围

#### 1.1 Backend 测试

**文件**：`backend/tests/test_base_handler.py`（新建）
```python
# 测试范围：
- test_handle_message_success()          # 正常消息处理
- test_handle_message_credit_deduction() # 积分扣费
- test_handle_message_credit_refund()    # 积分退回
- test_handle_message_no_credit()        # 积分不足
- test_handle_message_error_handling()   # 错误处理
- test_message_status_update()           # 消息状态更新
```

**文件**：`backend/tests/test_oss_service.py`（新建）
```python
# 测试范围：
- test_upload_from_url_success()         # 正常上传
- test_upload_file_size_limit()          # 文件大小限制
- test_upload_invalid_format()           # 格式校验
- test_upload_network_error()            # 网络错误
- test_upload_oss_error()                # OSS 错误
```

**文件**：`backend/tests/test_credit_service.py`（新建）
```python
# 测试范围：
- test_deduct_credit_success()           # 扣费成功
- test_deduct_credit_insufficient()      # 积分不足
- test_deduct_credit_race_condition()    # 并发扣费（锁机制）
- test_refund_credit_success()           # 退款成功
- test_refund_credit_idempotent()        # 重复退款幂等性
```

#### 1.2 Frontend 测试

**文件**：`frontend/src/contexts/__tests__/WebSocketContext.test.tsx`（新建）
```typescript
// 测试范围：
- 'should handle task_done_with_message'
- 'should handle task_status_update'
- 'should handle task_failure'
- 'should reconnect on disconnect'
- 'should cleanup on unmount'
```

### 实施步骤
1. **Day 1**：
   - 创建分支 `test/add-unit-tests`
   - 编写 `test_base_handler.py`（重点：积分逻辑）
   - 编写 `test_oss_service.py`（重点：边界条件）
   - 运行测试：`pytest backend/tests/ -v`

2. **Day 2**：
   - 编写 `test_credit_service.py`（重点：并发锁）
   - 编写 `WebSocketContext.test.tsx`
   - 确保覆盖率：`pytest --cov=backend/services --cov-report=term`
   - 合并到 main：`git checkout main && git merge test/add-unit-tests`

### 验收标准
- [ ] 所有新测试通过（绿色）
- [ ] 覆盖率：`base.py` ≥70%，`oss_service.py` ≥80%，`credit_service.py` ≥80%
- [ ] CI/CD 通过（如有）
- [ ] 已合并到 main

### 回滚预案
如果测试编写困难（如依赖外部服务），使用 Mock：
```python
from unittest.mock import AsyncMock, patch

@patch('services.oss_service.httpx.AsyncClient')
async def test_upload_from_url_success(mock_client):
    # Mock 外部依赖
    pass
```

---

## Phase 2：低风险修复（0.5天）

### 目标
修复不改变业务逻辑的代码质量问题。

### 2.1 添加 async try-except（5个文件）

#### 文件清单
1. `backend/services/handlers/base.py`
2. `backend/services/websocket_manager.py`
3. `backend/services/message_service.py`
4. `backend/services/credit_service.py`
5. `backend/services/sms_service.py`

#### 修改示例
**Before:**
```python
async def handle_message(self, message: str, user_id: str):
    result = await self.process(message)
    return result
```

**After:**
```python
async def handle_message(self, message: str, user_id: str):
    try:
        result = await self.process(message)
        return result
    except Exception as e:
        logger.error(f"handle_message failed: user_id={user_id}, error={e}")
        raise
```

#### 实施步骤
1. 创建分支 `fix/add-async-error-handling`
2. 逐个文件添加 try-except
3. 每修改一个文件，运行测试：`pytest backend/tests/test_xxx.py`
4. 合并到 main

### 2.2 移除 TypeScript `any`（3个文件）

#### 文件清单
1. `frontend/src/contexts/WebSocketContext.tsx`
2. `frontend/src/utils/messageUtils.ts`
3. `frontend/src/services/message.ts`

#### 修改示例
**Before:**
```typescript
handler: (msg: any) => void
```

**After:**
```typescript
interface WSMessageData {
  type: 'task_done_with_message' | 'task_status_update' | 'task_failure';
  task_id: string;
  message?: Message;
  status?: string;
  error?: string;
}

handler: (msg: WSMessageData) => void
```

#### 实施步骤
1. 创建分支 `fix/remove-typescript-any`
2. 定义 `WSMessageData` 接口
3. 替换所有 `any` 类型
4. 运行类型检查：`npm run type-check`
5. 合并到 main

### 验收标准
- [ ] 所有测试通过
- [ ] TypeScript 无类型错误
- [ ] 手动测试：发送消息、WebSocket 推送正常
- [ ] 已合并到 main

---

## Phase 3：高风险重构 - base.py（1-2天）🔴

### 目标
拆分 880 行的 `base.py` 为 3 个文件，不改变公开 API。

### 拆分方案

#### 目标结构
```
backend/services/handlers/
├── base.py                    # 300行（核心抽象）
├── credit_manager.py          # 200行（积分逻辑）NEW
└── message_helper.py          # 200行（消息处理）NEW
```

#### 文件职责

**1. `base.py`（核心抽象）**
- 保留：`BaseHandler` 类定义
- 保留：`handle_message()` 主流程
- 保留：子类必须实现的抽象方法
- 依赖：`CreditManager`、`MessageHelper`

**2. `credit_manager.py`（积分逻辑）NEW**
- 提取：`_deduct_credit()`
- 提取：`_refund_credit()`
- 提取：`_check_credit_sufficient()`
- 提取：积分相关的常量和工具函数

**3. `message_helper.py`（消息处理）NEW**
- 提取：`_create_message()`
- 提取：`_update_message_status()`
- 提取：`_format_error_message()`
- 提取：消息格式化相关工具函数

### 实施步骤

#### Day 1：重构 + 自测
1. **创建分支**：`refactor/split-base-handler`
2. **创建新文件**：
   ```bash
   touch backend/services/handlers/credit_manager.py
   touch backend/services/handlers/message_helper.py
   ```
3. **逐步迁移**（每迁移一个函数，运行测试）：
   - Step 1：迁移 `CreditManager` 类 → 测试
   - Step 2：迁移 `MessageHelper` 类 → 测试
   - Step 3：更新 `base.py` 的导入 → 测试
   - Step 4：更新所有子类的导入（如 `openai_handler.py`）→ 测试
4. **运行完整测试**：
   ```bash
   pytest backend/tests/test_base_handler.py -v
   pytest backend/tests/test_credit_service.py -v
   ```

#### Day 2：集成测试 + 手动验证
5. **手动回归测试清单**：
   - [ ] 发送文本消息（OpenAI）
   - [ ] 发送文本消息（Claude）
   - [ ] 发送文本消息（Google）
   - [ ] 发送图片消息 + 识别
   - [ ] 流式输出正常
   - [ ] 积分扣费正确
   - [ ] 积分不足报错
   - [ ] 任务失败退款
   - [ ] WebSocket 实时推送
6. **代码审查**：
   - [ ] 公开 API 未改变
   - [ ] 导入路径正确
   - [ ] 无循环依赖
   - [ ] 文件大小：`base.py` ≤350行
7. **合并到 main**：
   ```bash
   git checkout main
   git merge refactor/split-base-handler
   git push origin main
   ```

### 验收标准
- [ ] 所有单元测试通过
- [ ] 手动回归测试全部通过
- [ ] 文件大小：`base.py` ≤350行
- [ ] 无循环依赖（`import` 检查）
- [ ] 子类（OpenAI/Claude/Google handler）正常工作
- [ ] 已合并到 main

### 回滚预案
如果出现严重 bug：
```bash
git revert <commit-hash>
git push origin main
# 恢复到备份分支
git reset --hard backup/before-refactor-2026-02-13
```

---

## Phase 4：高风险重构 - oss_service.py（1天）🟠

### 目标
拆分 465 行的单函数文件为多个小函数。

### 拆分方案

**Before:**
```python
# oss_service.py (465行, 1个函数)
async def upload_from_url(url: str) -> str:
    # 下载 + 校验 + 上传逻辑混在一起
```

**After:**
```python
# oss_service.py (150行)
async def upload_from_url(url: str) -> str:
    """主流程编排"""
    file_data = await _download_with_size_check(url)
    _validate_format(file_data)
    oss_url = await _upload_to_oss(file_data)
    return oss_url

# oss_download.py (100行) NEW
async def _download_with_size_check(url: str) -> bytes:
    """下载并检查文件大小"""
    pass

# oss_validator.py (80行) NEW
def _validate_format(file_data: bytes) -> None:
    """校验文件格式"""
    pass

# oss_uploader.py (120行) NEW
async def _upload_to_oss(file_data: bytes) -> str:
    """上传到 OSS"""
    pass
```

### 实施步骤
1. **创建分支**：`refactor/split-oss-service`
2. **创建新文件**：
   ```bash
   touch backend/services/oss_download.py
   touch backend/services/oss_validator.py
   touch backend/services/oss_uploader.py
   ```
3. **逐步迁移**（每迁移一个函数，运行测试）：
   - Step 1：提取 `_download_with_size_check()` → 测试
   - Step 2：提取 `_validate_format()` → 测试
   - Step 3：提取 `_upload_to_oss()` → 测试
   - Step 4：简化 `upload_from_url()` 为流程编排 → 测试
4. **运行测试**：
   ```bash
   pytest backend/tests/test_oss_service.py -v
   ```
5. **手动测试**：
   - [ ] 上传图片（正常流程）
   - [ ] 上传视频（正常流程）
   - [ ] 上传超大文件（触发大小限制）
   - [ ] 上传非法格式（触发格式校验）
   - [ ] 网络错误（模拟超时）
6. **合并到 main**

### 验收标准
- [ ] 所有测试通过
- [ ] 手动测试全部通过
- [ ] `upload_from_url()` ≤50行（只负责流程编排）
- [ ] 已合并到 main

---

## Phase 5：高风险重构 - WebSocketContext.tsx（1天）🟠

### 目标
拆分 501 行的 WebSocket Context 为多个文件。

### 拆分方案

**目标结构：**
```
frontend/src/contexts/
├── WebSocketContext.tsx           # 200行（Context 定义 + Provider）
├── websocket/
│   ├── handlers.ts                # 150行（消息处理函数）NEW
│   ├── heartbeat.ts               # 80行（心跳逻辑）NEW
│   └── types.ts                   # 50行（类型定义）NEW
```

#### 文件职责

**1. `WebSocketContext.tsx`（核心 Provider）**
- 保留：Context 定义
- 保留：WebSocket 连接管理
- 保留：`useWebSocket` hook
- 依赖：`handlers`、`heartbeat`

**2. `websocket/handlers.ts`（消息处理）NEW**
- 提取：`handleTaskDoneWithMessage()`
- 提取：`handleTaskStatusUpdate()`
- 提取：`handleTaskFailure()`

**3. `websocket/heartbeat.ts`（心跳逻辑）NEW**
- 提取：`startHeartbeat()`
- 提取：`stopHeartbeat()`
- 提取：心跳间隔常量

**4. `websocket/types.ts`（类型定义）NEW**
- 提取：`WSMessageData` 接口
- 提取：`TaskErrorData` 接口
- 提取：其他 WebSocket 相关类型

### 实施步骤
1. **创建分支**：`refactor/split-websocket-context`
2. **创建新文件夹和文件**
3. **逐步迁移**（每迁移一个模块，运行测试）
4. **运行测试**：
   ```bash
   npm test -- WebSocketContext.test.tsx
   ```
5. **手动测试**：
   - [ ] WebSocket 连接成功
   - [ ] 消息实时推送
   - [ ] 任务状态更新
   - [ ] 心跳正常
   - [ ] 断线重连
   - [ ] 组件卸载清理
6. **合并到 main**

### 验收标准
- [ ] 所有测试通过
- [ ] 手动测试全部通过
- [ ] `WebSocketContext.tsx` ≤250行
- [ ] 已合并到 main

---

## Phase 6：中风险重构 - 其他大文件（0.5天）🟡

### 目标
拆分剩余超标文件。

### 文件清单
1. `backend/services/adapters/kie/chat_adapter.py` - 525行
2. `frontend/src/components/chat/ImagePreviewModal.tsx` - 499行
3. `backend/services/background_task_worker.py` - 353行
4. `backend/services/credit_service.py` - 283行
5. `backend/services/sms_service.py` - 225行

### 实施策略
- 逐个文件创建分支
- 提取独立函数到新文件
- 每个文件独立测试后合并

---

## Phase 7：最终验证（0.5天）

### 目标
全量测试 + 更新文档。

### 验收清单
- [ ] 运行完整测试套件：`pytest backend/tests/ -v`
- [ ] 运行前端测试：`npm test`
- [ ] 手动回归测试（完整流程）
- [ ] 更新 `PROJECT_OVERVIEW.md`
- [ ] 更新 `FUNCTION_INDEX.md`
- [ ] 更新 `CURRENT_ISSUES.md`（移除已修复问题）
- [ ] 提交最终报告：`docs/REFACTOR_REPORT_2026-02-13.md`

---

## 附录 A：合并前检查清单

每个 Phase 合并到 main 前必须检查：

### 代码质量
- [ ] 所有单元测试通过（`pytest` / `npm test`）
- [ ] 类型检查通过（`mypy` / `npm run type-check`）
- [ ] 无 linter 错误（`flake8` / `eslint`）
- [ ] 代码审查通过（如有团队成员）

### 功能验证
- [ ] 手动回归测试通过（见各 Phase 清单）
- [ ] 无新增错误日志（检查 `loguru` 输出）
- [ ] 性能无明显下降（如有压测）

### 文档同步
- [ ] 更新 `FUNCTION_INDEX.md`（如有函数改动）
- [ ] 更新 `PROJECT_OVERVIEW.md`（如有文件新增/移动）
- [ ] 添加 commit message（遵循规范）

### Git 规范
- [ ] Commit message 符合规范（`feat/fix/refactor:...`）
- [ ] 分支已推送到远程
- [ ] 合并无冲突

---

## 附录 B：回滚预案

### 场景 1：单次 commit 引入 bug
```bash
# 回滚最后一次提交
git revert HEAD
git push origin main
```

### 场景 2：整个 Phase 需要回滚
```bash
# 查找 Phase 开始的 commit
git log --oneline

# 回滚到 Phase 开始前
git revert <phase-start-commit>^..<current-commit>
git push origin main
```

### 场景 3：需要完全回到重构前
```bash
# 恢复到备份分支状态
git checkout backup/before-refactor-2026-02-13
git checkout -b main-rollback
git push -f origin main-rollback

# 通知团队：main 已回滚到 main-rollback
```

### 场景 4：灰度发布失败（如有）
```bash
# 回滚负载均衡配置
# 将流量从新版本切回旧版本
# 具体操作取决于部署架构
```

---

## 附录 C：风险等级定义

| 等级 | 定义 | 示例 |
|-----|------|------|
| 🔴 极高 | 核心基类，影响全局 | `base.py` |
| 🟠 高 | 关键服务，影响主流程 | `oss_service.py`、`WebSocketContext.tsx` |
| 🟡 中 | 独立服务，影响单一功能 | `sms_service.py` |
| 🟢 低 | 类型/错误处理，不改逻辑 | TypeScript `any`、async try-except |

---

## 📞 执行中的沟通

### 每日汇报
- 当前 Phase 进度
- 遇到的问题
- 明天计划

### 异常上报
- 测试失败：立即报告，不强行推进
- 手动测试发现 bug：立即回滚，分析原因
- 不确定的技术决策：咨询后再执行

---

**计划制定完成时间**：2026-02-13
**预计开始时间**：用户确认后立即开始
**预计完成时间**：2026-02-18 ~ 2026-02-20

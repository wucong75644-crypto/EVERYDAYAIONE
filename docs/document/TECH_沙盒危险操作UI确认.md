# 沙盒危险操作 UI 确认方案

> 状态：待实施
> 优先级：中
> 参考：Claude Code Permission Check 机制

## 背景

沙盒内删除文件等危险操作，之前用 `confirm_delete` 参数做二次确认，但 AI 不可靠地使用这个参数，导致删除成功率很低。当前已移除门控（commit 2b8cf75），靠路径白名单 + _bak_ 备份兜底。

长期方案：像 Claude Code 一样，前端弹出硬确认弹窗，用户不点确认就不执行。

## 目标

code_execute 执行过程中遇到危险操作（删除/覆写）时：
1. 后端暂停执行，向前端推送确认请求
2. 前端弹出弹窗，展示操作详情
3. 用户点「确认」→ 后端继续执行；点「取消」→ 后端返回 PermissionError
4. 超时（30秒无响应）→ 自动取消

## 架构设计

### 信号流

```
code_execute 执行中
  ↓
scoped_os.remove("文件.xlsx") 触发
  ↓
sandbox_worker 发送确认请求到主进程（通过 Queue）
  ↓
主进程 → WebSocket 推送 confirm_request 到前端
  ↓
前端弹出确认弹窗：
  "AI 要删除以下文件：
   - 文件.xlsx
   [确认删除] [取消]"
  ↓
用户点击 → 前端发送 confirm_response 到后端
  ↓
后端 → Queue 传回 sandbox_worker
  ↓
sandbox_worker 继续/中止执行
```

### WebSocket 消息格式

```typescript
// 后端 → 前端
{
  type: "sandbox_confirm_request",
  data: {
    request_id: "uuid",
    task_id: "task-uuid",
    operation: "delete",          // delete | overwrite
    files: ["文件.xlsx"],         // 涉及的文件列表
    description: "删除 1 个文件",  // 人类可读描述
    timeout_seconds: 30,
  }
}

// 前端 → 后端
{
  type: "sandbox_confirm_response",
  data: {
    request_id: "uuid",
    approved: true | false,
  }
}
```

### 前端组件

```
ConfirmDialog（模态弹窗）
├── 标题："代码执行确认"
├── 内容：操作描述 + 文件列表
├── 按钮：[确认执行] [取消]
├── 倒计时：30秒自动取消
└── 样式：与现有 ask_user 弹窗一致
```

## 实施步骤

### Phase 1：后端确认协议（2天）

1. `scoped_os.py` — remove/unlink 触发确认请求（通过 multiprocessing.Queue 与主进程通信）
2. `sandbox_worker.py` — 新增确认等待逻辑（阻塞等 Queue 响应，超时取消）
3. `kernel_worker.py` — 同步适配 Kernel 模式
4. `sandbox_tool_mixin.py` — 接收确认请求，推送 WS，等待响应

### Phase 2：WebSocket 协议（1天）

1. `schemas/websocket.py` — 新增 `build_sandbox_confirm_request`
2. `chat_handler.py` — 处理 `sandbox_confirm_response` 消息
3. WebSocket 路由注册

### Phase 3：前端弹窗（2天）

1. `ConfirmDialog` 组件（复用现有 Modal 基础组件）
2. WS 消息监听 + 状态管理
3. 倒计时 UI + 自动取消
4. 与现有 tool_step 展示整合

### Phase 4：测试与调优（1天）

1. 单元测试：确认/取消/超时 3 条路径
2. E2E 测试：完整 WS 交互流程
3. 边界：多个连续删除、大批量删除合并为一次确认

## 注意事项

- confirm 请求必须包含 task_id，前端用 task_id 关联到正确的对话
- 多个删除操作应合并为一次确认（如用户说"删除所有文件"，不要弹 14 次）
- 合并策略：同一次 code_execute 中的所有 remove 调用，收集后一次性确认
- Kernel 模式（有状态沙盒）需要特殊处理——进程间通信走 Unix Socket

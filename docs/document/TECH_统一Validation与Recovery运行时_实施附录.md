# 统一 Validation 与 Recovery Runtime 实施附录

> 主设计：`TECH_统一Validation与Recovery运行时.md`
> 日期：2026-07-19

## 1. 边界场景

| 场景 | 处理策略 | 模块 |
|---|---|---|
| 空工具集合 | 保持普通文本结束语义 | Completion |
| 未知动态工具 | 明确`FATAL`，不跳过Schema后执行 | Input |
| 参数可安全强转 | 记录normalized，不算失败 | Input |
| 缺必填参数 | 结构化Observation；模型纠正或询问用户 | Recovery |
| 工具抛异常 | 归一为唯一Terminal Result | Normalizer |
| 返回普通字符串 | 兼容归一，禁止关键词成为主要事实 | Normalizer |
| AgentResult partial/empty | 保留语义，不当作普通成功 | Normalizer |
| 并行工具部分失败 | 独立Receipt；整轮按最严重状态决策 | Runtime |
| 相同调用重复三次 | 调用指纹判定无进展并wrap-up | Tracker |
| 同错误文本包含动态ID | 规范化后再生成指纹 | Tracker |
| 取消发生在工具完成同时 | cancellation优先；Actor fencing防止晚提交 | Chat/Actor |
| Lease丢失 | 取消执行，不提交Receipts | Actor |
| 结果过大 | Receipt不含结果正文 | Persistence |
| Provider换模型 | 保留原工具Receipt，模型重试不重复副作用 | Executor |
| 用户刷新或换通道 | 数据库事实恢复，不依赖进程内Tracker展示 | Actor/UI |

## 2. 可观测性

统一日志事件：

- `validation_input_rejected`
- `validation_result_classified`
- `validation_recovery_decided`
- `validation_loop_stalled`
- `validation_completion_checked`
- `validation_terminal_committed`

必带字段：

```text
conversation_id, task_id, org_id, model_step, tool_call_id,
tool_name, result_class, decision, attempt, fingerprint
```

禁止记录工具完整参数、文件内容、代码、Token、URL和用户隐私数据。

上线指标：

- 首次工具失败率。
- 自动恢复成功率。
- 同错误重复率。
- 平均恢复轮次和Token。
- `UNKNOWN`副作用结果数。
- Tool Call/Result不配对数，目标为0。
- running步骤未闭合数，目标为0。
- Web与企微终态不一致数，目标为0。

## 3. 测试设计

### 3.1 单元测试

- 所有枚举和状态转换。
- AgentResult、字符串、异常、超时、空、partial、cancelled归一化。
- 输入Schema、缺参、类型纠正和未知工具。
- 错误指纹稳定性与脱敏。
- 同类错误、连续失败和进展重置。
- 各ToolEffect下的恢复决策。
- Completion只接受满足合同的成功终态。
- Receipt序列化和大小限制。

### 3.2 集成测试

- 主Chat：失败→Observation→模型纠正→成功→最终回答。
- ERP Agent：与主Chat产生相同分类和决策。
- 多工具一成功一失败。
- Provider失败与工具失败不会嵌套重试。
- 上下文压缩恢复与工具纠错分别计数。
- 副作用`UNKNOWN`不重放。
- 取消、超时、预算耗尽和wrap-up。
- Tool Call/Result严格配对。

### 3.3 Actor与数据库测试

- Receipt与消息、Artifact、ContextItem原子提交。
- lease丢失不提交Receipt。
- Actor重试不会重复Receipt。
- 旧12参数RPC继续工作。
- 13参数RPC字段、数量、租户和唯一约束。
- migration/rollback真实PostgreSQL验证。

### 3.4 回归场景

刚才两个Binder错误只作为通用回归样本：

- 第一次失败能形成`RETRY_MODEL`。
- 纠正成功后Run正常完成。
- 中间错误保留审计但不成为最终回答。
- 不在Runtime中出现DuckDB、SQL或字段名专属分支。

## 4. 实施阶段

### Phase 1：协议与观察模式

1. 新增统一类型、Normalizer、Tracker和Recovery纯函数。
2. 接入主Chat与ToolLoopExecutor，旧逻辑继续掌权。
3. 新Runtime只记录新旧决策差异。
4. 补齐全部单元测试。

### Phase 2：主Chat权威切换

1. 主Chat使用统一决策控制继续、wrap-up和失败。
2. CompletionGate只消费统一成功终态。
3. 保持Actor外层Provider重试不变。
4. 完成Web与企微端到端测试。

### Phase 3：专业Agent权威切换

1. ToolLoopExecutor改为消费统一Runtime。
2. 删除旧分类、Tracker和重复停止判断。
3. ERP与ScheduledTask完成回归。

### Phase 4：持久化与生产验证

1. 执行migration 140。
2. Actor原子提交Validation Receipts。
3. 部署后核对自动恢复、重复错误和终态闭合指标。
4. 生产验证通过后删除观察模式比较代码，禁止永久双轨。

## 5. 部署与回滚

部署顺序：

1. 先部署向后兼容数据库迁移。
2. 部署观察模式代码。
3. 验证新旧决策差异。
4. 切主Chat权威模式。
5. 切专业Agent权威模式。
6. 清理观察模式。

回滚：

- 权威模式异常时回滚到上一应用版本，旧12参数RPC仍可用。
- 新表和13参数RPC为增量结构，不影响旧应用。
- 确认无新版本应用写入后执行rollback migration，删除13参数重载与Receipt表。
- 不回滚或修改已经提交的消息、Artifact、积分和Context revision。

## 6. 风险

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| 两层同时重试导致放大 | 高 | 每类错误唯一Recovery Owner |
| 副作用工具重复执行 | 高 | UNKNOWN状态；默认禁止自动重放 |
| 观察模式永久存在 | 中 | Phase 4强制删除双轨 |
| 旧字符串结果误分类 | 中 | 兼容Normalizer，结构化信号优先 |
| Receipt写放大 | 低 | 每任务最多100条，不存正文 |
| 主Chat与专业Agent行为变化 | 中 | 分阶段权威切换和同一回归矩阵 |
| 文档与实现再次漂移 | 中 | 更新函数索引、概览和当前问题 |

## 7. 验收标准

1. 所有模型循环使用同一ResultClass、Tracker和RecoveryDecision。
2. 每个Tool Call恰好一个Terminal Result。
3. 参数纠错、工具纠错、Provider重试和上下文恢复互不混用。
4. 副作用UNKNOWN不会被自动重放。
5. 同一错误不会无限重复。
6. Web与企微获得相同终态。
7. Actor lease丢失不提交任何运行产物。
8. Validation Receipt与Turn终态原子提交。
9. 不存在SQL、表格、ERP或Skill专属分支。
10. 新旧专项、Actor、上下文和全量相关测试通过。

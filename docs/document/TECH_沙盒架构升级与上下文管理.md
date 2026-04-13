# 沙盒架构升级与上下文管理

> 版本：V1.0 | 日期：2026-04-13 | 状态：方案确认，分批实施

## 一、背景与问题

### 核心场景
系统需要处理每月 30-100 万条订单数据，用户上传 Excel 文件后，Agent 需要：
1. 读取理解文件结构
2. 与用户确认计算方案
3. 全量读取 + 计算 + 输出结果文件

### 发现的问题链
1. **沙盒与工作区割裂**：code_execute 无法访问用户上传的文件（已修复，WORKSPACE_DIR）
2. **工具描述缺失**：LLM 不知道 WORKSPACE_DIR 存在（已修复，架构隔离工具描述）
3. **文件注入指引错误**：workspace 提示词指引 file_read 读 Excel（已修复，按扩展名分类）
4. **Excel 读取速度慢**：openpyxl 读 56MB 需 120s（已修复，calamine 引擎 3s）
5. **工具结果二次截断**：sandbox 8K 截断后 envelope 再砍到 2K → Agent 循环重试
6. **budget 系统单维度**：只有时间维度，sandbox 超时不受 budget 约束
7. **staging 数据不清理**：磁盘膨胀风险
8. **Agent 行为模式错误**：反复 print(df) 全量数据进上下文 → token 爆炸

## 二、三层架构设计

### 架构总览
```
第一层：Output Control（输出控制）— 控制工具结果进入上下文的大小
第二层：Budget System（预算系统）— 多维预算控制执行边界
第三层：Data Lifecycle（数据生命周期）— staging 数据过期清理
```

### 对标大厂
| 策略 | 来源 | 我们的实现 |
|------|------|----------|
| 当前结果不截断（<50K） | Claude Code Tier 0 | code_execute/file_* 免二次截断 |
| 旧结果机械清除 | Claude Code Tier 2 + JetBrains | observation masking，keep_turns=3 |
| Code-as-Query | OpenAI/Claude 通用 | 工具描述强化，禁止 print(df) |
| 多维预算 | OpenAI Agents SDK + AutoGen | turns + tokens + wall_time |
| 子 Agent 共享父预算 | OpenAI Agents SDK | fork() 机制 |
| 容器级数据销毁 | OpenAI 20min / Claude 30天 | staging 3 天过期 |

## 三、Phase 2 — 输出控制（第一批实施）

### 3.1 code_execute/file_* 免二次截断

**文件**: `services/agent/tool_result_envelope.py`

**改动**: 将 code_execute 和 file_* 工具加入 `_NO_TRUNCATE` 白名单

**理由**:
- sandbox 自有 `max_result_chars=8000` 截断（第一层）
- tool_result_envelope 再截到 2000（第二层）= 双重截断
- 5 个文件表头 5581 字符 → 被砍到 493 → Agent 循环重试
- 大厂做法：Claude Code < 50K 不截断，OpenAI 走 ace_tools 侧通道

```python
_NO_TRUNCATE = {
    "generate_image", "generate_video",
    "code_execute",
    "file_read", "file_write", "file_list", "file_search", "file_info",
}
```

### 3.2 工具描述强化 Code-as-Query

**文件**: `config/code_tools.py`

**核心原则**: LLM 写代码处理数据，数据永远不进上下文。只有代码和汇总进上下文。

**主 Agent 版 `_DESCRIPTION_WORKSPACE` 关键段落**:
```
数据分析工作流：
1. 先读表头了解结构: pd.read_excel(file, engine='calamine', nrows=5)
2. 检查数据质量（空值/异常值/重复），如有问题先告知用户
3. 确认计算方案后，一个 code_execute 完成全部: 读取→计算→输出
4. 数据量大(>50MB)时优先输出 CSV（打开更快）

注意：
- 禁止反复打开文件探索——读一次表头，想好方案，一步到位
- 禁止 print(df) / print(df.to_string())——用 df.shape, df.describe(), df.head()
- 大结果写文件输出，只 print 确认信息
```

**Token 效率对比**:
| 模式 | 100 万行处理 | Token 消耗 |
|------|------------|-----------|
| ❌ print(df) 全量 | 不可能 | 100M+ token |
| ✅ Code-as-Query | 5 轮完成 | ~5K token |

### 3.3 TOOL_SYSTEM_PROMPT 数据处理规范

**文件**: `config/chat_tools.py`

新增数据分析请求的分层处理规范：
```
### 数据分析请求（工作区文件）
1. 读表头了解结构: pd.read_excel(file, engine='calamine', nrows=5)
2. 检查数据质量，如发现问题先告知用户
3. 说明计算方案和公式，等用户确认
4. 一个 code_execute 完成全部计算+输出
禁止反复打开文件探索——读一次表头，想好方案，一步到位。
```

### 3.4 keep_turns 2→3

**文件**: `core/config.py`

```python
context_tool_keep_turns: int = 3  # 从 2 改为 3，多步分析需要更多上下文
```

## 四、Phase 3 — 数据生命周期（第一批实施）

### 4.1 staging 过期清理

**文件**: `main.py`

**策略**: 服务启动时扫描 staging/，删除 mtime > 3 天的子目录

**对标**:
- OpenAI: 容器 20 分钟空闲过期，数据随容器销毁
- Claude API: 容器 30 天过期
- 行业共识: 没有大厂做会话内自动清理，都是靠过期销毁

**不做会话内清理的原因**:
- 大厂都不做（OpenAI/Claude/E2B/JupyterHub 无一例外）
- 一个对话内 30 次查询 ≈ 150-600MB，不会触发磁盘问题
- staging 数据不进 LLM 上下文，不影响 token 消耗

```python
# main.py lifespan 启动时
async def _cleanup_stale_staging():
    staging_root = Path(settings.file_workspace_root) / "staging"
    if not staging_root.exists():
        return
    cutoff = time.time() - 3 * 86400  # 3 天
    for child in staging_root.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            logger.info(f"Cleaned stale staging | dir={child.name}")
```

## 五、Phase 1 — 多维预算系统（第二批实施）

### 5.1 ExecutionBudget 重写

**文件**: `services/agent/execution_budget.py`

```python
class ExecutionBudget:
    def __init__(
        self,
        max_turns: int = 15,
        max_tokens: int = 100_000,
        max_wall_time: float = 180.0,
    ):
        self._turns_used = 0
        self._tokens_used = 0
        self._max_turns = max_turns
        self._max_tokens = max_tokens
        self._max_wall_time = max_wall_time
        self._start = time.monotonic()
        self._parent = None

    def use_turn(self) -> None: ...
    def use_tokens(self, n: int) -> None: ...

    @property
    def stop_reason(self) -> str | None:
        if self._turns_used >= self._max_turns: return "max_turns"
        if self._tokens_used >= self._max_tokens: return "max_tokens"
        if self.wall_elapsed >= self._max_wall_time: return "wall_timeout"
        return None

    def fork(self, max_turns: int = 10) -> "ExecutionBudget":
        """子 Agent 预算 — 共享 token/time，独立 turn 上限"""
        child = ExecutionBudget(
            max_turns=max_turns,
            max_tokens=self._max_tokens - self._tokens_used,
            max_wall_time=self.wall_remaining,
        )
        child._parent = self
        return child

    # 向后兼容旧接口
    def check_or_log(self, context: str) -> bool:
        reason = self.stop_reason
        if reason:
            logger.warning(f"ExecutionBudget expired | context={context} | reason={reason}")
            return False
        return True

    @property
    def remaining(self) -> float: ...     # wall_time 剩余
    def tool_timeout(self, max_per_tool=30.0) -> float: ...  # 保持兼容
```

### 5.2 配置统一

**文件**: `core/config.py`

```python
# 多维预算配置
budget_max_turns: int = 15              # 主 Agent 最大轮次
budget_max_tokens: int = 100_000        # 单次对话 token 上限
budget_max_wall_time: float = 180.0     # 安全网（秒）
```

### 5.3 主循环改造

**文件**: `services/handlers/chat_handler.py`

```python
# 改前：
_budget = ExecutionBudget(60.0)
for turn in range(MAX_TOOL_TURNS):
    if not _budget.check_or_log(...): break

# 改后：
_budget = ExecutionBudget(
    max_turns=_s.budget_max_turns,
    max_tokens=_s.budget_max_tokens,
    max_wall_time=_s.budget_max_wall_time,
)
while not _budget.stop_reason:
    _budget.use_turn()
    ...
    _budget.use_tokens(turn_tokens)
```

### 5.4 子 Agent fork

**文件**: `services/agent/tool_executor.py` → `erp_agent.py`

```python
parent_budget = getattr(self, "_budget", None)
child_budget = parent_budget.fork(max_turns=10) if parent_budget else ExecutionBudget(...)
result = await agent.execute(query, budget=child_budget)
```

### 5.5 优雅降级

```python
stop = _budget.stop_reason
if stop and not accumulated_text:
    STOP_MESSAGES = {
        "max_turns": "查询涉及多个步骤，已达到单次对话工具调用上限。请缩小查询范围或分步提问。",
        "max_tokens": "本次查询消耗的数据量过大，请缩小查询范围。",
        "wall_timeout": "查询耗时过长，请稍后重试。",
    }
    await self.on_error(task_id, "BUDGET_EXCEEDED", STOP_MESSAGES[stop])
elif stop and accumulated_text:
    accumulated_text += f"\n\n> ⚠️ 已达到执行上限（{stop}），以上为部分结果。"
```

### 5.6 企微路径补 budget

**文件**: `services/handlers/chat_generate_mixin.py`

同主循环，创建 budget 并用 stop_reason 控制循环。

### 5.7 涉及文件清单

| 文件 | 改动 |
|------|------|
| execution_budget.py | 重写：多维 + fork + stop_reason + 向后兼容 |
| chat_handler.py | while stop_reason + token 累加 + 优雅降级 |
| chat_generate_mixin.py | 企微路径补 budget |
| erp_agent.py | 用 fork 继承父预算 |
| tool_executor.py | 传 fork budget 给子 Agent |
| scheduled_task_agent.py | 同 ERP |
| config.py | 统一配置 |
| erp_agent_types.py | 删 ERP_AGENT_DEADLINE，从 fork 获取 |

## 六、实施计划

| 批次 | Phase | 文件数 | 风险 | 预估 |
|------|-------|--------|------|------|
| 第一批 | Phase 2 (输出控制) + Phase 3 (staging) | 5 个 | 低 | 1h |
| 第二批 | Phase 1 (多维预算) | 8 个 | 中 | 3h |

## 七、验收标准

### 第一批验收
- [ ] 5 个文件表头读取不被截断，Agent 不循环重试
- [ ] Agent 使用 engine='calamine' 读 Excel
- [ ] Agent 不 print(df) 全量数据
- [ ] staging 3 天过期自动清理
- [ ] 全量测试通过

### 第二批验收
- [ ] budget 多维控制（turns + tokens + wall_time）
- [ ] ERP Agent fork 父预算
- [ ] 超限时优雅降级（不返回空）
- [ ] 企微路径有 budget 控制
- [ ] 全量测试通过

## 八、Token 效率预期

| 场景 | 数据量 | 预期轮次 | 预期 Token |
|------|--------|---------|-----------|
| "这些文件是干什么的" | - | 1-2 轮 | ~1K |
| "计算每个运营的总体积" | 56MB Excel | 3-4 轮 | ~5K |
| "分析订单异常" | 100 万行 | 4-5 轮 | ~7K |
| "生成月度报表" | 100 万行 | 5-6 轮 | ~8K |

# TECH_工具结果分流架构

> 版本：v1.0 | 日期：2026-04-15 | 状态：方案待确认

## 一、问题背景

### 1.1 事故现场

用户查询"GMBJZ01-06 在哪些店铺在卖"，耗时 2 分 14 秒，ERPAgent 被调用 3 次。

**根因链路**：

```
local_platform_map_query → 返回 user_id（无店铺名）
  → LLM 调 local_shop_list → 全量 6138 字符
    → envelope 截断到 3024，信号提示："用 get_persisted_result() 获取"
      → LLM 写沙盒代码调 get_persisted_result() → NameError（函数未注册到沙盒）
        → 重试 → token 爆了 → 空结果 → 主循环再来 × 3
```

### 1.2 核心缺陷

1. **截断信号指向不存在的函数**：`get_persisted_result()` 存在于后端内存（ContextVar），但从未注册到沙盒。沙盒是纯计算引擎，也不应该注册。
2. **大数据走文本截断而非文件分流**：所有 `local_*` 工具直接返回格式化文本，超长时做事后截断。大厂（OpenAI/Claude Code）的做法是超阈值时数据落盘文件，LLM 只看摘要。
3. **只有 `local_db_export` 走 staging**：其他 `local_*` 工具都不写 staging，沙盒无法读取完整数据。

## 二、目标架构

### 2.1 对标大厂

| 平台 | 大数据处理方式 | LLM context 内容 |
|------|--------------|-----------------|
| OpenAI Code Interpreter | 数据存 `/mnt/data/`，沙盒 `pd.read_csv()` 读 | 仅摘要（行数+列名+前几行） |
| Claude Code | 超阈值写 `tool-results/<id>.txt` | preview 前 2KB + 文件路径 |
| **我们（改后）** | 超阈值写 `staging/tool_result_xxx.txt` | 摘要 + staging 路径提示 |

### 2.2 分流规则

```
工具返回结果
    │
    ├── len(result) ≤ 阈值
    │   └── 直接放入 LLM context（原样，不截断）
    │
    └── len(result) > 阈值
        ├── 完整数据 → 写入 staging/tool_result_{tool}_{hash}.txt
        └── LLM context 放摘要：
            "{首行}\n{数据行数}\n{前几行预览}\n
             完整数据已存入 staging/tool_result_xxx.txt，
             可用 code_execute 中 read_file('staging/tool_result_xxx.txt') 读取。"
```

**阈值**：复用现有预算配置（主 Agent 2000 字符 / ERP Agent 3000 字符）。

## 三、现有架构盘点

### 3.1 已有零件

| 零件 | 位置 | 状态 |
|------|------|------|
| staging 目录 | `{workspace_root}/staging/{conversation_id}/` | ✅ |
| staging 路径计算 | `local_db_export`、`tool_executor`、`sandbox/functions` 统一公式 | ✅ |
| 沙盒 `read_file("staging/xxx")` | `sandbox/functions.py:309` | ✅ |
| envelope 截断层 | `tool_result_envelope.py` | ✅ |
| ContextVar 并发隔离 | `tool_result_envelope.py:35` | ✅ |
| 延迟清理 staging | `erp_agent.py:434` | ✅ |

### 3.2 缺失环节

| 缺失 | 说明 |
|------|------|
| envelope 不知道 staging 路径 | 纯函数，无 conversation_id / workspace_root |
| 超阈值写文件逻辑 | 不存在，现在只做文本截断 |
| 摘要生成 | 截断后仍是原始文本片段，没有结构化摘要 |

## 四、详细设计

### 4.1 新增 ContextVar：staging_dir

在 `tool_result_envelope.py` 中，和现有的 `_persisted_ctx` 并列：

```python
# 新增：staging 目录路径（请求级，ContextVar 并发隔离）
_staging_dir_ctx: ContextVar[Optional[str]] = ContextVar(
    "staging_dir", default=None,
)

def set_staging_dir(path: str) -> None:
    """设置当前请求的 staging 目录路径"""
    _staging_dir_ctx.set(path)

def get_staging_dir() -> Optional[str]:
    """获取当前请求的 staging 目录路径"""
    return _staging_dir_ctx.get()

def clear_staging_dir() -> None:
    """清理 staging 目录路径"""
    _staging_dir_ctx.set(None)
```

### 4.2 改造 `_smart_truncate` → 分流逻辑

**改造前**（截断 + 内存暂存 + 错误信号）：

```python
def _smart_truncate(tool_name, result, budget):
    truncated = _truncate_erp(result, budget)  # 文本截断
    persist_key = persist_and_get_key(tool_name, result)  # 存内存
    signal = f'可用 code_execute 调用 get_persisted_result("{persist_key}") 获取。'  # ← BUG
    return truncated + signal
```

**改造后**（staging 落盘 + 摘要 + 正确信号）：

```python
def _smart_truncate(tool_name, result, budget):
    staging_dir = get_staging_dir()
    if staging_dir is None:
        raise RuntimeError(
            f"staging_dir 未设置，无法分流工具结果（tool={tool_name}）。"
            "请确保在工具循环入口调用了 set_staging_dir()。"
        )

    # 落盘 staging 文件
    rel_path = _persist_to_staging(staging_dir, tool_name, result)
    # 生成摘要（首行 + 数据条数 + 前几行预览 + 路径提示）
    summary = _build_summary(tool_name, result, budget)
    signal = (
        f"\n完整数据（{len(result)} 字符）已存入 {rel_path}，"
        f'可用 code_execute 中 read_file("{rel_path}") 读取。'
    )
    return summary + signal
```

> **无降级分支**：staging_dir 为 None 时直接抛异常。conversation_id 在请求入口就确定了，staging 路径一定可用。如果为 None 说明调用方漏了 `set_staging_dir()`，应该报错暴露 bug，而不是静默降级让问题继续隐藏。这与 OpenAI/Claude Code 的设计一致——容器创建时就确保文件目录存在。

### 4.3 新增 `_persist_to_staging`

```python
def _persist_to_staging(staging_dir: str, tool_name: str, result: str) -> str:
    """将完整结果写入 staging 文件，返回相对路径（供 read_file 使用）"""
    from pathlib import Path

    Path(staging_dir).mkdir(parents=True, exist_ok=True)

    digest = hashlib.md5(result.encode()).hexdigest()[:8]
    filename = f"tool_result_{tool_name}_{digest}.txt"
    file_path = (Path(staging_dir) / filename).resolve()

    file_path.write_text(result, encoding="utf-8")

    # 计算相对路径：staging/{conversation_id}/{filename}
    # read_file 要求以 "staging/" 开头
    # 从 workspace_root 推导，避免路径中恰好包含 "staging" 字样导致误定位
    from core.config import get_settings as _gs
    _ws_root = Path(_gs().file_workspace_root).resolve()
    rel_path = str(file_path.relative_to(_ws_root))

    logger.info(
        f"ToolResultEnvelope staged | tool={tool_name} | "
        f"chars={len(result)} | path={rel_path}"
    )
    return rel_path
```

### 4.4 新增 `_build_summary`

摘要需要携带元数据，让 LLM 知道数据来源和时效：

```python
def _build_summary(tool_name: str, result: str, budget: int) -> str:
    """从完整结果生成摘要（元数据头 + 首行 + 数据条数 + 前几行预览）"""
    from datetime import datetime

    lines = result.split("\n")
    non_empty = [l for l in lines if l.strip()]

    # 元数据头（工具名 + 时间戳）
    meta = f"[数据来源: {tool_name} | 获取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"

    # 首行（通常是标题/汇总行，如"共 50 个店铺："）
    first_line = lines[0] if lines else ""

    # 汇总行
    summary_lines = [l for l in lines if _SUMMARY_LINE_RE.search(l.strip())]

    # 数据预览（前几行，不超预算的 60%）
    preview_budget = int(budget * 0.6)
    preview_lines = []
    used = len(meta) + len(first_line)
    for line in non_empty[1:]:
        if line in summary_lines:
            continue
        if used + len(line) + 1 > preview_budget:
            break
        preview_lines.append(line)
        used += len(line) + 1

    parts = [meta, first_line]
    if preview_lines:
        parts.extend(preview_lines)
        parts.append(f"... 共 {len(non_empty)} 行数据")
    if summary_lines:
        parts.extend(summary_lines)

    return "\n".join(parts)
```

LLM 看到的摘要示例：

```
[数据来源: local_shop_list | 获取时间: 2026-04-15 11:01:49]
共 50 个店铺：

【淘宝】(12个)
  1. 蓝创旗舰店 (ID:123456)
  2. 蓝创专卖店 (ID:789012)
... 共 58 行数据

完整数据（6138 字符）已存入 staging/default/tool_result_local_shop_list_a1b2c3d4.txt，
可用 code_execute 中 read_file("staging/default/tool_result_local_shop_list_a1b2c3d4.txt") 读取。
```

### 4.5 调用方设置 staging_dir

三个入口的 ContextVar 继承关系：

```
chat_handler._stream_generate()  ← asyncio.create_task，独立 context
  └── set_staging_dir()
      └── _execute_tool_calls()
          └── tool_executor._erp_agent()
              └── ERPAgent.execute()          ← 同步 await，继承父 context ✅
                  └── tool_loop.run()
                      └── wrap_for_erp_agent() → _smart_truncate()  ← 能读到 staging_dir ✅

scheduled_task_agent.execute()   ← 独立入口，不经过 chat_handler
  └── set_staging_dir()          ← 必须自己设，否则 ContextVar 为 None → RuntimeError
      └── tool_loop.run()
          └── wrap_for_erp_agent() → _smart_truncate()  ← 能读到 staging_dir ✅
```

**文件 1：`chat_handler.py`**（主 Agent 工具循环入口）

```python
# chat_handler.py _stream_generate() 方法，在 while not _budget.stop_reason 循环前：
from services.agent.tool_result_envelope import set_staging_dir, clear_staging_dir
from core.config import get_settings as _get_settings
from pathlib import Path

_s = _get_settings()
_staging_dir = str(
    Path(_s.file_workspace_root) / "staging" / (conversation_id or "default")
)
set_staging_dir(_staging_dir)
# ... 在 finally 块中：
clear_staging_dir()
```

**文件 2：`scheduled_task_agent.py`**（定时任务独立入口）

```python
# scheduled_task_agent.py execute() 方法，在 tool_loop.run() 前后：
from services.agent.tool_result_envelope import set_staging_dir, clear_staging_dir
from core.config import get_settings
from pathlib import Path

settings = get_settings()
staging_dir = str(
    Path(settings.file_workspace_root) / "staging" / (self.conversation_id or "default")
)
set_staging_dir(staging_dir)
try:
    result = await tool_loop.run(...)
finally:
    clear_staging_dir()
```

**文件 3：`erp_agent.py`**（ERPAgent，继承 + 兜底）

```python
# erp_agent.py execute() 方法，在 tool_loop.run() 前：
from services.agent.tool_result_envelope import set_staging_dir
from core.config import get_settings
from pathlib import Path

settings = get_settings()
staging_dir = str(
    Path(settings.file_workspace_root) / "staging" / (self.conversation_id or "default")
)
set_staging_dir(staging_dir)
# 注意：ERPAgent 只 set 不 clear，由最外层（chat_handler / scheduled_task_agent）的 finally 统一 clear
```

> ERPAgent 通过同步 `await` 调用链运行在 `chat_handler` 的同一 async context 内，正常情况下会继承父 context 的值。这里再 set 一次是为了：1）ERPAgent 独立运行时也安全；2）覆盖无副作用。**只在最外层 clear，避免 ERPAgent clear 后 chat_handler 后续工具调用读不到。**

### 4.6 `wrap_erp_agent_result()` 适配

`wrap_erp_agent_result()` 在 ERPAgent 结果外面套"禁止改写"信封。分流后的处理顺序：

```python
def wrap_erp_agent_result(result: str) -> str:
    # 1. 先走 wrap() — 超阈值时 result 已被替换为摘要+staging路径
    truncated = wrap("erp_agent", result, budget=ERP_AGENT_RESULT_BUDGET)
    # 2. 再套信封 — 信封包的是摘要（不是原文），这是正确的
    #    因为信封的目的是防止主 Agent 改写数据，摘要里的数字/日期同样需要保护
    return "⚠ 以下是 ERP 数据查询的最终结果..." + truncated + "─── ERP 结果结束 ───"
```

**不需要改动**：现有的 `wrap_erp_agent_result()` 逻辑天然兼容。信封包在摘要外面是正确的。

### 4.7 清理遗留

- 删除 `_smart_truncate` 中引用 `get_persisted_result` 的信号文案
- `persist_and_get_key()` / `get_persisted()` / `clear_persisted()` 暂时保留（其他地方可能用到），但不再从截断信号中引用
- `_persisted_ctx` ContextVar 保留，后续可清理
- `clear_staging_dir()` 放在 `chat_handler.py:539` 的 `clear_persisted()` 旁边（同一个 finally 块）
- **`wrap()` 防重入检查更新**：现有的 `if "⚠ 输出已截断" in result` 需改为 `if "已存入" in result and "read_file" in result`，匹配新信号格式
- **删除废弃函数**：`_truncate_erp()`、`_truncate_code()`、`_truncate_search()` 改造后不再被调用，直接删除，避免死代码

## 五、文件清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `services/agent/tool_result_envelope.py` | **核心改动** | 新增 staging ContextVar + `_persist_to_staging` + `_build_summary` + 改造 `_smart_truncate` + 摘要元数据（工具名+时间） |
| `services/handlers/chat_handler.py` | 接入 | `_stream_generate()` 前后 set/clear staging_dir |
| `services/agent/scheduled_task_agent.py` | 接入 | `execute()` 前后 set/clear staging_dir |
| `services/agent/erp_agent.py` | 接入 + 清理修复 | `execute()` 前 set staging_dir + 删除 `_cleanup_staging_delayed` 调用（清理权归最外层） |

## 六、清理机制同步

### 6.1 现有清理存在的问题

ERPAgent 执行完后 5 分钟延迟清理 staging，但主 Agent 工具循环可能还没结束。摘要里写了 `read_file("staging/xxx.txt")`，这个提示留在 messages 里——文件被删后 LLM 按提示去读 → 文件不存在 → 报错 → 重试循环。

### 6.2 清理原则

**谁创建会话，谁清理 staging。子调用不清理。**

| 入口 | 清理权 | 原因 |
|------|--------|------|
| `chat_handler.py` | ✅ `_stream_generate` finally 中延迟清理 | 最外层，工具循环全部结束后清理 |
| `scheduled_task_agent.py` | ✅ `execute()` finally 中延迟清理 | 独立入口，任务结束后清理 |
| `erp_agent.py` | ❌ **删除现有的 `_cleanup_staging_delayed` 调用** | 子调用，不应清理父会话的 staging |

### 6.3 具体改动

**`erp_agent.py`**：删除 `asyncio.create_task(self._cleanup_staging_delayed())` 调用（line 116）。`_cleanup_staging_delayed` 方法可保留（独立测试场景可能用到），但不在正常执行路径中调用。

**`chat_handler.py`**：在 `_stream_generate()` 的 finally 块中新增延迟清理：

```python
# chat_handler.py _stream_generate() finally 块：
finally:
    clear_staging_dir()
    # 延迟清理 staging 文件（会话级，5 分钟后）
    asyncio.create_task(_delayed_cleanup_staging(conversation_id))
```

```python
async def _delayed_cleanup_staging(conversation_id: str, delay: int = 300) -> None:
    """会话级 staging 延迟清理"""
    import shutil
    from pathlib import Path
    from core.config import get_settings
    try:
        await asyncio.sleep(delay)
        settings = get_settings()
        staging_dir = Path(settings.file_workspace_root) / "staging" / (conversation_id or "default")
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
            logger.info(f"Chat staging cleaned | dir={staging_dir}")
    except Exception as e:
        logger.debug(f"Chat staging cleanup failed | error={e}")
```

**`scheduled_task_agent.py`**：保持现有清理不变（它是独立入口，清理自己的 staging 没问题）。

**`main.py` 启动时全局清理**：保持现有的 3 天过期清理不变（兜底安全网）。

## 七、不需要改的

| 组件 | 为什么不用改 |
|------|------------|
| 沙盒 `SandboxExecutor` | `read_file("staging/xxx")` 已支持 |
| 沙盒注册函数 `build_sandbox_executor` | 不新增函数，复用 `read_file` |
| 所有 `local_*` 工具 | 工具只管返回数据，分流由 envelope 层自动处理 |
| `tool_loop_executor.py` | 已调用 `wrap_for_erp_agent()`，自动走新逻辑 |
| `chat_tool_mixin.py` | 已调用 `wrap()`，自动走新逻辑 |
| `main.py` 启动清理 | 保持现有 3 天过期全局清理不变 |

## 八、改造前后对比

### 改造前（当前）

```
local_shop_list → 6138 字符
  → wrap_for_erp_agent() 截断到 3024
  → 信号："用 get_persisted_result('xxx') 获取"
  → LLM 写 code_execute → NameError
  → 重试 → token 爆 → 空结果 × 3
```

### 改造后

```
local_shop_list → 6138 字符
  → wrap_for_erp_agent() 检测超阈值
  → 完整数据写 staging/tool_result_local_shop_list_a1b2c3d4.txt
  → LLM 看到摘要："共 50 个店铺：... 完整数据已存入 staging/xxx.txt，
     可用 code_execute 中 read_file('staging/xxx.txt') 读取"
  → LLM 写 code_execute: data = read_file("staging/xxx.txt")
  → 沙盒正常执行 ✅
```

## 九、边界场景

| 场景 | 处理 |
|------|------|
| staging_dir 未设置（ContextVar 为 None） | 直接抛 RuntimeError，暴露调用方 bug |
| 同一工具多次被调用（hash 相同） | 文件名含 hash，相同结果复用同一文件 |
| staging 文件被提前清理 | 复用现有延迟清理机制（5 分钟后清理） |
| 结果是错误信息（以 ❌ 开头） | 错误信息通常很短，不会触发分流 |
| `code_execute` / `generate_image` 等免截断工具 | `_NO_TRUNCATE` 白名单不变，不经过分流 |
| 非 ERP 工具超阈值（`web_search` 等） | 同样走分流，`_build_summary` 通用逻辑（首行+预览）适用所有工具类型 |

## 十、任务等级与工作量

**A 级任务**：涉及 4 个文件 + 清理机制改造 + 边界场景处理。

| 文件 | 改动量 |
|------|--------|
| `tool_result_envelope.py` | ~80 行（核心：分流 + 落盘 + 摘要） |
| `chat_handler.py` | ~15 行（set/clear + 延迟清理） |
| `scheduled_task_agent.py` | ~5 行（set/clear） |
| `erp_agent.py` | ~5 行（set + 删除旧清理调用） |

# TECH: Export 子进程隔离

> 版本：v1.0 | 日期：2026-04-25 | 状态：方案讨论

## 1. 背景与问题

### 1.1 崩溃链路（生产实录 2026-04-24）

```
22:18:42  erp_agent(mode=summary) → ✅ 2s 完成
22:19:24  erp_agent(mode=export)  → ⏰ DuckDB 25s 超时
22:19:58  LLM 重试 export         → 💀 worker 崩溃
22:20:08  systemd 重启             → WebSocket 断开，用户看到"卡住"
```

### 1.2 根因

export 通过 `asyncio.to_thread` 在 chat worker 进程内执行 DuckDB 导出：
- DuckDB + postgres_scanner 从 PG 拉全天订单（5-20万行），内存不可控
- 25s 看门狗 `conn.interrupt()` 不保证杀死后台线程 → 孤儿线程
- LLM 收到超时后重试 → 第二个 DuckDB 线程叠加 → OOM 杀 worker
- worker 崩溃 → WebSocket 断开 → 前端卡死

### 1.3 生产实测数据（2026-04-25 基准测试）

| 场景 | 行数 | 列数 | 子进程冷启动耗时 | Parquet 大小 |
|---|---|---|---|---|
| 1 天 | 31,757 | 11 | 6.3s | 540 KB |
| 1 周 | 235,902 | 11 | ~7s | 3.9 MB |
| 1 月 | 1,156,559 | 11 | ~8s | 14.2 MB |
| 100 万 | 1,000,000 | 11 | ~7s | 14.5 MB |
| 全量 | 4,839,012 | 11 | ~10s | 34.1 MB |
| **100 万** | **1,000,000** | **168 全列** | **OOM** | 💀 DuckDB 256MB 不够 |

关键发现：
- **11 列 export 速度极快**：全量 484 万行只需 10s，120s 超时绰绰有余
- **全列 export 必然 OOM**：100 万行 × 168 列 → DuckDB 256MB 内存耗尽（244MB/244MB）
- **DEFAULT_DETAIL_FIELDS 是 11 列**（order 类型），正常路径不会 OOM
- **之前崩溃的根因**：PG 侧负载波动 + DuckDB 首次 catalog 扫描（168 列表结构）+ `order_classifier.to_case_sql()` 额外内存开销 → 偶发超时 → LLM 重试 → 孤儿线程叠加 → OOM

### 1.3 设计目标

- DuckDB 导出在独立进程执行，崩了不影响 chat worker
- 上下游接口完全不变（erp_agent 返回 ToolOutput + file_ref，code_execute 读 staging parquet）
- 超时可控，进程级 kill 无孤儿

---

## 2. 方案概述

**一句话**：把 `_export()` 中的 `asyncio.to_thread(engine.export_to_parquet)` 替换为 `asyncio.create_subprocess_exec(python, export_worker.py)`。

```
现在（线程，共享内存）：
  _export() → asyncio.to_thread(engine.export_to_parquet, query, path)
                ↓ 同进程
              DuckDB COPY → PG 拉数据 → 写 Parquet

改后（子进程，内存隔离）：
  _export() → asyncio.create_subprocess_exec(export_worker.py)
                ↓ 独立进程
              DuckDB COPY → PG 拉数据 → 写 Parquet
                ↓ stdout JSON
              {"row_count": 12345, "size_kb": 4567.8, "path": "/staging/xxx.parquet"}
```

对上下游完全透明：
- erp_agent 仍返回 `ToolOutput(file_ref=staging/xxx.parquet)` — 不变
- code_execute 仍 `read_file("staging/xxx.parquet")` — 不变
- 主 Agent tool loop — 不变
- 前端 — 不变

---

## 3. 改动范围

### 3.1 新增文件

| 文件 | 说明 |
|------|------|
| `backend/core/export_worker.py` | 子进程入口，接收参数执行 DuckDB 导出，结果写 stdout |

### 3.2 修改文件

| 文件 | 改动 |
|------|------|
| `backend/services/kuaimai/erp_unified_query.py` | `_export()` L526-528：`to_thread` → subprocess 调用 |
| `backend/services/agent/erp_agent.py` | `run_step()` L183：export 模式超时从 30s → 120s |
| `backend/core/config.py` | 新增 `export_subprocess_timeout: int = 120` |

### 3.3 不改的文件

- `duckdb_engine.py` — `export_to_parquet` 原封不动，子进程复用
- `erp_duckdb_helpers.py` — `resolve_export_path` 不变
- `department_agent.py` — `_query_local_data` 不变
- `tool_loop_executor.py` — 不变
- `chat_handler.py` / `chat_tool_mixin.py` — 不变
- 前端 — 不变

---

## 4. 实现细节

### 4.1 `export_worker.py` — 子进程入口

```python
"""DuckDB Export 子进程

独立进程执行 DuckDB → Parquet 导出，内存隔离不影响 chat worker。

通信协议：
- stdin：JSON 参数（一次性读完）
- stdout：最终结果 JSON（一行，进程退出前写入）
- stderr：进度行（每行一个 JSON，父进程实时读取推送 thinking）

进度行格式：
  {"phase":"connect"}                          — 正在连接数据库
  {"phase":"export","elapsed":12.3}            — 正在导出（每 5s 上报一次）
  {"phase":"done","row_count":15000,"size_kb":4567.8,"elapsed":45.2}  — 导出完成
  {"phase":"error","message":"..."}            — 导出失败
"""

import json
import sys
import time
import threading
from pathlib import Path


def _report(phase: str, **kwargs) -> None:
    """向 stderr 写一行进度 JSON（父进程实时读取）"""
    json.dump({"phase": phase, **kwargs}, sys.stderr)
    sys.stderr.write("\n")
    sys.stderr.flush()


def _size_monitor(output_path: str, stop: threading.Event) -> None:
    """后台线程：每 5s 检查 parquet 文件大小，上报进度。

    DuckDB COPY TO 是原子操作无回调，但文件在写入过程中大小持续增长，
    通过监控文件大小可以间接反映导出进度。
    """
    t0 = time.monotonic()
    while not stop.wait(5.0):
        try:
            p = Path(output_path)
            if p.exists():
                size_kb = p.stat().st_size / 1024
                _report("export", elapsed=round(time.monotonic() - t0, 1),
                        size_kb=round(size_kb, 1))
        except OSError:
            pass


def main() -> None:
    params = json.loads(sys.stdin.read())
    t0 = time.monotonic()

    _report("connect")

    from core.duckdb_engine import DuckDBEngine

    engine = DuckDBEngine(
        pg_url=params["pg_url"],
        memory_limit=params.get("memory_limit", "256MB"),
        threads=params.get("threads", 2),
    )

    output_path = params["output_path"]
    stop = threading.Event()
    monitor = threading.Thread(
        target=_size_monitor, args=(output_path, stop), daemon=True,
    )
    monitor.start()

    try:
        result = engine.export_to_parquet(
            query=params["query"],
            output_path=output_path,
            timeout=params.get("timeout", 120.0),
        )
        stop.set()
        elapsed = round(time.monotonic() - t0, 1)
        _report("done", row_count=result["row_count"],
                size_kb=round(result["size_kb"], 1), elapsed=elapsed)

        # 最终结果写 stdout
        json.dump(result, sys.stdout)

    except Exception as e:
        stop.set()
        _report("error", message=str(e)[:500])
        raise
    finally:
        stop.set()
        engine.close()


if __name__ == "__main__":
    main()
```

通信协议设计：
- **stdout 只写最终结果**：一个 JSON 对象，与 `export_to_parquet` 返回值一致
- **stderr 写进度行**：每行一个 JSON，父进程逐行读取推送 thinking
- **文件大小监控**：DuckDB `COPY TO` 无进度回调，但 parquet 文件在写入过程中持续增长，后台线程每 5s 读文件大小上报
- **phase 枚举**：`connect` → `export`(每5s) → `done`/`error`，父进程可据此推送不同的 thinking 文案

### 4.2 `erp_unified_query.py._export()` — 调用方改造

替换 L526-528 的 `asyncio.to_thread` 调用：

```python
# ── 原代码（L526-528）──
# result = await _asyncio.to_thread(
#     engine.export_to_parquet, query, staging_path,
# )

# ── 新代码 ──
result = await self._subprocess_export(query, str(staging_path))
```

新增 `_subprocess_export` 方法：

```python
async def _subprocess_export(
    self, query: str, output_path: str,
    push_thinking: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, int | float | str]:
    """子进程执行 DuckDB 导出，内存隔离，实时进度推送。

    Args:
        query:          完整 SELECT SQL
        output_path:    Parquet 输出路径
        push_thinking:  进度回调（推送 thinking 给前端）
    """
    import asyncio as _aio

    settings = _get_settings()
    timeout = settings.export_subprocess_timeout  # 默认 120s

    params = json.dumps({
        "query": query,
        "output_path": output_path,
        "pg_url": settings.database_url,
        "memory_limit": settings.duckdb_memory_limit,
        "threads": settings.duckdb_threads,
        "timeout": timeout - 5,  # 留 5s 给进程启动和清理
    })

    proc = await _aio.create_subprocess_exec(
        sys.executable, "-m", "core.export_worker",
        stdin=_aio.subprocess.PIPE,
        stdout=_aio.subprocess.PIPE,
        stderr=_aio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[1]),  # backend/
    )

    # 写入参数后关闭 stdin，子进程开始执行
    proc.stdin.write(params.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    # 实时读 stderr 进度行，推送 thinking
    async def _read_progress():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            if push_thinking:
                try:
                    progress = json.loads(line)
                    text = _format_progress(progress)
                    if text:
                        await push_thinking(text)
                except (json.JSONDecodeError, KeyError):
                    pass  # 非进度行（DuckDB 日志等），忽略

    try:
        # 并行：读进度 + 等子进程结束
        progress_task = _aio.create_task(_read_progress())
        stdout = await _aio.wait_for(proc.stdout.read(), timeout=timeout)
        await proc.wait()
        progress_task.cancel()
    except _aio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"Export subprocess timed out after {timeout}s"
        )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Export subprocess failed (exit={proc.returncode})"
        )

    return json.loads(stdout.decode())


def _format_progress(progress: dict) -> str | None:
    """将子进程进度 JSON 格式化为用户可读的 thinking 文案。"""
    phase = progress.get("phase")
    if phase == "connect":
        return "正在连接数据库..."
    if phase == "export":
        size = progress.get("size_kb", 0)
        elapsed = progress.get("elapsed", 0)
        if size > 1024:
            return f"正在导出数据... {size / 1024:.1f}MB 已写入（{elapsed:.0f}s）"
        return f"正在导出数据... {size:.0f}KB 已写入（{elapsed:.0f}s）"
    if phase == "done":
        rows = progress.get("row_count", 0)
        size = progress.get("size_kb", 0)
        elapsed = progress.get("elapsed", 0)
        if size > 1024:
            return f"导出完成：{rows:,} 行，{size / 1024:.1f}MB（{elapsed:.0f}s）"
        return f"导出完成：{rows:,} 行，{size:.0f}KB（{elapsed:.0f}s）"
    return None  # error phase 不推 thinking（走异常处理）
```

`_export()` 调用时传入 `push_thinking`：

```python
# _export() 中的调用
result = await self._subprocess_export(
    query, str(staging_path),
    push_thinking=push_thinking,  # 从 execute() 透传下来
)
```

设计要点：
- **实时读 stderr**：`proc.stderr.readline()` 逐行读取，每收到一行立即推送 thinking
- **进度可视**：用户在 thinking 折叠区看到 `正在导出数据... 12.5MB 已写入（15s）`
- **不阻塞结果**：进度读取和 stdout 结果读取并行，互不干扰
- `proc.kill()`：进程级 SIGKILL，无孤儿线程，DuckDB 连接随进程回收
- `timeout - 5`：子进程内部超时比外部早 5s，优先走内部优雅超时（有清理），外部 kill 是兜底
- `cwd=backend/`：确保子进程能 import `core.duckdb_engine`

### 4.3 `push_thinking` 透传链路

现有链路中 `_push_thinking` 在 `erp_agent.py` 里，`_export()` 在 `erp_unified_query.py` 里，需要把回调传下去：

```
erp_agent._execute()
  → erp_agent._push_thinking("查询订单数据...")     ← 已有
  → agent.execute(params=step.params)
    → TradeAgent._dispatch()
      → _query_local_data(mode="export", ...)
        → UnifiedQueryEngine.execute(mode="export", ...)
          → _export(push_thinking=push_thinking)     ← 新增参数透传
            → _subprocess_export(push_thinking=...)   ← 实时推进度
```

改动点：
1. `UnifiedQueryEngine.execute()` — 新增可选参数 `push_thinking`，仅 export 模式传递给 `_export()`
2. `department_agent._query_local_data()` — 透传 `push_thinking`（从 `self._push_thinking` 取）
3. `_export()` — 透传给 `_subprocess_export()`

都是参数透传，无逻辑变更。summary/detail 模式不传此参数，完全不受影响。

### 4.4 `profile_parquet` — 保留在主进程

`profile_parquet`（L558-560）**不改**，仍用 `asyncio.to_thread`：
- 它读的是已写入磁盘的 parquet 文件，内存占用由文件大小决定
- SUMMARIZE + top-5 统计，内存峰值远小于 export（export 是 PG 全量拉取）
- 子进程启动有开销（1-2s），profile 没必要再开一个

### 4.4 `erp_agent.py` — export 超时放宽

`run_step()` L181-184：

```python
# ── 原代码 ──
# result = await asyncio.wait_for(
#     agent.execute(...),
#     timeout=min(remaining, 30.0),
# )

# ── 新代码 ──
step_timeout = 30.0
if step.params.get("mode") == "export":
    step_timeout = min(remaining, 130.0)  # export 给 130s（含子进程 120s + profile 10s）
else:
    step_timeout = min(remaining, 30.0)

result = await asyncio.wait_for(
    agent.execute(query[:200], dag_mode=True, params=step.params),
    timeout=step_timeout,
)
```

超时层次调整后：

| 层 | 原超时 | 新超时（export） | 说明 |
|---|---|---|---|
| erp_agent 全局 | 180s | 180s 不变 | |
| run_step wait_for | 30s | **130s** | 含子进程 + profile |
| 子进程外层 | 无 | **120s** | `wait_for(proc.communicate)` |
| 子进程内部 DuckDB | 25s | **115s** | `export_to_parquet(timeout=)` |
| summary/detail | 30s | 30s 不变 | 不影响 |

### 4.5 `config.py` — 新增配置

```python
export_subprocess_timeout: int = 120  # export 子进程超时（秒）
```

---

## 5. 错误处理矩阵

| 场景 | 现象 | 处理 |
|------|------|------|
| 导出成功 | stdout 返回 JSON | 正常流程 |
| DuckDB 内部超时 | 子进程 `export_to_parquet` 抛 `TimeoutError`，exit code ≠ 0 | `_subprocess_export` 抛 RuntimeError → erp_agent 返回 timeout 诊断 |
| 子进程 OOM | OS 杀死子进程，exit code = -9 | `_subprocess_export` 抛 RuntimeError → erp_agent 返回 error 诊断 |
| 子进程启动失败 | FileNotFoundError / ImportError | `_subprocess_export` 抛异常 → 现有异常处理兜底 |
| 外层超时（120s） | `proc.communicate` 超时 | `proc.kill()` + 抛 TimeoutError → erp_agent 返回 timeout 诊断 |
| PG 连接失败 | 子进程 DuckDB ATTACH 失败 | exit code ≠ 0，stderr 含错误信息 |

所有错误路径都走现有的 erp_agent 异常处理（`run_step` L185-187 的 except），不需要新增错误处理逻辑。

---

## 6. 子进程 vs 线程 对比

| 维度 | 线程（现在） | 子进程（改后） |
|------|------------|--------------|
| 内存隔离 | ❌ 共享，DuckDB 峰值影响 worker | ✅ 独立地址空间 |
| 崩溃隔离 | ❌ OOM 杀整个 worker | ✅ 只杀子进程 |
| 超时清理 | ❌ `conn.interrupt()` 不保证线程终止 | ✅ `proc.kill()` SIGKILL 必杀 |
| 孤儿残留 | ❌ 协程取消后线程还在跑 | ✅ 进程死了就死了 |
| 启动开销 | ~0ms | ~1-2s（Python 解释器 + import） |
| 实现复杂度 | 低 | 中（stdin/stdout IPC） |

启动开销 1-2s 在 120s 的 export 中可忽略。

---

## 7. 验证计划

1. **单元测试**：mock subprocess，验证参数传递、JSON 解析、超时 kill、错误处理
2. **本地集成测试**：实际导出 1 天订单，确认 parquet 文件生成正确
3. **崩溃模拟**：在 export_worker.py 中注入 OOM（分配大内存），确认 chat worker 不受影响
4. **生产验证**：部署后执行"导出昨天订单做分析"，观察全链路正常
5. **回归**：summary/detail 模式不受影响（不走子进程）

---

## 8. 用户可见效果

### 8.1 thinking 折叠区（导出过程中）

```
── ERP Agent ──
→ 查询订单数据...
→ 正在连接数据库...
→ 正在导出数据... 2.3MB 已写入（5s）
→ 正在导出数据... 8.7MB 已写入（10s）
→ 正在导出数据... 15.2MB 已写入（15s）
→ 导出完成：152,347 行，18.5MB（18s）
→ 完成
```

### 8.2 对比现在

| | 现在 | 改后 |
|---|---|---|
| 导出过程 | 黑盒，25s 后突然超时 | 每 5s 上报进度（大小+耗时） |
| 超时处理 | 孤儿线程 + LLM 重试 + OOM | 子进程 kill 干净，erp_agent 返回诊断 |
| 大数据量 | 25s 内必须完成，否则失败 | 120s 窗口，20 万行订单有足够时间 |

---

## 9. 改动量估算

| 文件 | 新增/修改 | 行数 |
|------|----------|------|
| `core/export_worker.py` | 新增 | ~70行 |
| `erp_unified_query.py` | 修改 `_export` + 新增 `_subprocess_export` + `_format_progress` | ~80行 |
| `erp_agent.py` | 修改 `run_step` | ~5行（超时判断） |
| `department_agent.py` | 透传 `push_thinking` | ~3行 |
| `config.py` | 新增配置 | ~1行 |
| 测试 | 新增 | ~80行 |

总计约 **240 行**改动，5 个文件 + 1 个新文件。

---

## 10. 附带修复：LLM 重试防护

子进程隔离解决了崩溃问题，但 LLM 超时后盲目重试仍会浪费时间。调研 Claude Code 源码后，对齐其"先诊断再换策略"的设计。

### 10.1 现状问题

| 检查点 | 现状 | 问题 |
|---|---|---|
| `_diagnose_error` 文本 | "建议缩小时间范围后**重试**" | 直接引导 LLM 重试 |
| timeout 的 is_error | `status="timeout"`, `is_error=False` | LLM 感知不到这是失败 |
| TOOL_SYSTEM_PROMPT | "错误 → 告知用户" | 没有"先诊断"的引导 |

### 10.2 修复（3 处，共 ~10 行）

**改动 1 — 提示词**（`chat_tools.py` L116）：
```
# 原：- 错误 → 告知用户并建议替代方案
# 改：- 错误 → 先诊断失败原因，再决定是换参数重试还是告知用户，不要盲目重试
```

**改动 2 — is_error 判断**（`chat_handler.py`，AgentResult 处理处）：
```python
# 原：return (tc, result, result.status == "error")
# 改：return (tc, result, result.status in ("error", "timeout"))
```
让 timeout 也标记为 is_error=True，LLM 能正确感知失败。

**改动 3 — 诊断文本去掉重试引导**（`param_converter.py` `_diagnose_error`）：
```
# 原："查询超时，建议缩小时间范围后重试"
# 改："查询超时，可能原因：时间范围过大 / 数据量过多"
```
中性描述，让 LLM 自己决策。

### 10.3 设计依据

对齐 Claude Code（`claw-code/rust/crates/runtime/src/prompt.rs`）：
- 系统提示词：`"If an approach fails, diagnose the failure before switching tactics."` — 引导诊断而非盲目重试
- tool_result：`is_error: true` 标志位让 LLM 知道工具失败，不注入额外指令文字
- 错误信息原文直传，由 LLM 自己判断该不该重试

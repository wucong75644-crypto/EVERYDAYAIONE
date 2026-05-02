# 有状态代码执行（Stateful Code Execution）

## 背景

当前 code_execute 每次启动全新子进程，变量不保留。用户做财务分析时模型把计算拆成多次调用，每次都要重新读文件，浪费严重（实测 9 次 code_execute 消耗 170K tokens）。

行业标准（ChatGPT Code Interpreter）使用有状态 Jupyter Kernel，变量跨调用保留。

## 当前架构

```
用户消息 → LLM 生成 code_execute 调用
  → SandboxExecutor.execute()
  → subprocess.Popen（全新 Python 子进程）
  → sandbox_worker.py 执行代码
  → 进程退出，变量销毁
```

核心文件：
- `backend/services/sandbox/executor.py`（SandboxExecutor）
- `backend/services/sandbox/sandbox_worker.py`（子进程入口）
- `backend/services/sandbox/sandbox_constants.py`（白名单模块）
- `backend/services/sandbox/functions.py`（build_sandbox_executor）
- `backend/services/agent/tool_executor.py`（_code_execute 调用入口）

## 目标架构

```
用户消息 → LLM 生成 code_execute 调用
  → KernelManager.get_or_create(conversation_id)
  → 复用已有 Python 进程（变量还在）
  → 执行代码，变量保留
  → 空闲超时后进程回收
```

## 核心改动

### 1. KernelManager 进程池
- 按 conversation_id 分配持久 Python 进程
- 复用已有进程（变量跨调用保留）
- 空闲超时回收（对标 ChatGPT 20 分钟）
- 最大进程数限制（防内存溢出）

### 2. 沙盒安全加固
- 当前一次性子进程天然隔离（进程销毁=清理完成）
- 长驻进程需要：内存上限 + CPU 时间限制 + 文件系统隔离
- 参考 gVisor / nsjail / seccomp

### 3. 崩溃恢复
- 进程 OOM/段错误后自动重建
- 用户看到"环境已重置，请重新执行"
- 不丢失 workspace 文件（文件在磁盘，不在进程内存）

### 4. 提示词适配
改造后的 code_execute 提示词（有状态版本）：

```
### code_execute — Python 计算环境

有状态沙盒：同一对话内变量跨调用保留。
第一次读取的 DataFrame 后续可以直接使用，不需要重复读文件。

核心能力：
- 可用库：pd, plt, Path, math, json, datetime, Decimal, Counter, io
- 变量在对话期间持续存在（df, fig, result 等下次调用仍可用）
- 生成的文件写到 OUTPUT_DIR，平台自动检测上传
- 图表用 plt.savefig(OUTPUT_DIR + '/图.png', dpi=150, bbox_inches='tight')
- 写 Excel 用 engine='xlsxwriter'
- 用 print() 输出文本结果

使用方式：
- 第一步：读文件 + 初步探索（df = pd.read_excel(...)、df.head()、df.describe()）
- 后续步骤：直接操作已有变量（df.groupby(...)、df.plot(...)）
- 每步都可以 print 中间结果，根据结果决定下一步

注意事项：
- 环境可能因超时被重置，如果变量不存在请重新读取文件
- 不要用 code_execute 读取大数据文件——大文件用 data_query 查询后再操作
- 禁止 import os/sys
```

## 过渡方案（在有状态改造完成前）

当前 stateless 环境的临时提示词：

```
### code_execute — 计算与文件生成

无状态沙盒：每次调用都是全新进程，变量不保留。
所有计算必须写在一段代码中一次完成——读文件、计算、输出、导出放在同一次调用里。

核心能力：
- 可用库：pd, plt, Path, math, json, datetime, Decimal, Counter, io
- 生成的文件写到 OUTPUT_DIR，平台自动检测上传
- 图表用 plt.savefig(OUTPUT_DIR + '/图.png', dpi=150, bbox_inches='tight')
- 写 Excel 用 engine='xlsxwriter'
- 用 print() 输出文本结果

注意事项：
- 不要用 code_execute 读取大数据文件——大文件用 data_query 查询
- 禁止 import os/sys
```

## 实施步骤

1. KernelManager 实现（进程池 + 超时回收）
2. SandboxExecutor 改造（复用进程替代新建进程）
3. 安全加固（内存限制 + CPU 限制）
4. 崩溃恢复（进程异常重建）
5. 提示词切换（stateless → stateful）
6. 测试验证

## 参考

- ChatGPT Code Interpreter：gVisor 沙盒 + Jupyter Kernel + /mnt/data 持久文件系统 + 20 分钟超时
- OpenAI Codex Sandbox：容器级隔离 + 10 分钟超时
- Jupyter Enterprise Gateway：多用户 Kernel 管理
- 当前项目 sandbox：`backend/services/sandbox/`

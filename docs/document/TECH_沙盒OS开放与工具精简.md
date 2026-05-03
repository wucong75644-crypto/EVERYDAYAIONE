# TECH: 沙盒 OS 开放与工具精简

> 版本：v1.0 | 日期：2026-05-03 | 状态：方案设计

## 1. 项目上下文

### 架构现状

沙盒系统有两种执行模式：
- **Stateless Subprocess**（主模式）：每次 code_execute 启动新 `multiprocessing.Process(spawn)`，零状态残留
- **Stateful Kernel**（可选）：每对话一个长驻 REPL 进程（20 分钟空闲超时，最多 4 个并发 kernel）

安全分 7 层：AST 校验 → 模块黑名单 → 函数黑名单 → 资源限制 → 环境变量清理 → cwd 隔离 → builtins.open 白名单替换。

工具体系中有 5 个文件工具（file_list/file_search/file_read/file_write/file_edit）+ code_execute + data_query，功能存在大量重叠。

### 可复用模块

- `_global_scoped_open`：已有路径白名单 + realpath 防符号链接 + 文件名纠错。os 开放后仍复用这套路径安全机制
- `auto_upload`（`executor.py:342-391`）：基于 mtime/size 快照检测新文件 → CDN 上传。不受 os 开放影响
- `_build_sandbox_globals`：构建受限执行环境，可在此注入 scoped os 模块
- `SAFE_BUILTINS`：白名单已含 type/isinstance/hasattr 等反射操作，os.path 纯计算无安全风险

### 设计约束

- **sandbox_worker 是子进程**：不能直接传 Python 对象，通过 `multiprocessing.Queue` 序列化通信
- **kernel_worker 是长驻进程**：每次执行需 `_reset_security()` 防止用户代码篡改安全层
- **[FILE] 标记流**：sandbox stdout → executor result → tool_result_envelope → ToolLoopExecutor Phase 3 提取 → content_blocks → 前端
- **auto_upload 只扫 OUTPUT_DIR**（`workspace/下载/`），不扫 STAGING_DIR——这是设计意图，staging 是临时数据
- **workspace 隔离**：企业用户 `org/{org_id}/{user_id}`，个人用户 `personal/{hash}`，进程级 cwd 隔离

### 潜在冲突

- `file_list` 的**元数据自动附加**（前 5 个文件的列名/行数/类型）——os.listdir 不做这个。需保留轻量的元数据注入机制
- `file_read` 的 **PDF 文本提取 + 图片多模态返回**——os.open 读 PDF 只能拿到二进制字节。这些特殊能力必须保留
- `data_query` 的 **DuckDB SQL 引擎**——比 pandas 快 10x，大数据场景不可替代。保留

---

## 2. 问题分析

### 2.1 工具调用冗余

一个"读文件并画图"任务当前需要 3-5 轮工具调用：
```
file_list → data_query(explore) → data_query(sql) → code_execute
```
每轮重传完整上下文（5K tokens），4 轮 = 20K tokens 浪费 + 8-12 秒延迟。

### 2.2 LLM 错误放大

4 次独立的工具调用 = 4 次出错机会。典型问题：
- file_list 返回文件名，LLM 抄错到 data_query 的 file 参数
- data_query explore 返回列名，LLM 抄错到 SQL
- 每次出错只能等下一轮修——上下文越来越长，注意力越来越分散

### 2.3 os 被一刀切禁止

`os` 模块中 `os.path`/`os.listdir`/`os.walk`/`os.stat` 对数据分析无害且高度有用，但因为 `os.system`/`os.environ`/`os.exec*` 的存在被整体禁止。代价是必须维护 5 个文件工具来重新实现 os 的安全子集。

---

## 3. 设计方案

### 3.1 核心思路

**在沙盒内提供受限 os 模块**（只暴露安全操作），让 LLM 在一次 code_execute 中完成"发现文件 → 读取 → 处理 → 输出"全链路。

### 3.2 受限 os 模块实现

```python
# services/sandbox/scoped_os.py（新文件）

"""沙盒受限 os 模块 — 只暴露安全文件操作，屏蔽系统命令和进程操作"""

import os as _real_os
from pathlib import Path as _Path


def build_scoped_os(workspace_dir: str, staging_dir: str, output_dir: str):
    """构建受限 os 模块实例（每次执行构造一份）

    安全原则：
    - 路径操作：所有接受路径的函数自动 resolve + 白名单校验
    - 只读操作：listdir/walk/stat/path.* 直接放行
    - 写操作：makedirs/rename 限制在 workspace 内
    - 删除操作：remove/rmdir 拦截（返回错误提示，需用户确认）
    - 系统命令：system/popen/exec* 不暴露（属性不存在）
    - 环境变量：environ 返回空 dict
    """

    _allowed_prefixes = [
        _real_os.path.realpath(workspace_dir),
        _real_os.path.realpath(staging_dir) if staging_dir else None,
        _real_os.path.realpath(output_dir) if output_dir else None,
        _real_os.path.realpath("/tmp"),
    ]
    _allowed_prefixes = [p for p in _allowed_prefixes if p]

    def _check_path(path_str: str) -> str:
        """路径安全校验 — 解析相对路径 + realpath + 白名单"""
        if not _real_os.path.isabs(path_str):
            path_str = _real_os.path.join(workspace_dir, path_str)
        resolved = _real_os.path.realpath(path_str)
        if not any(
            resolved.startswith(prefix + _real_os.sep) or resolved == prefix
            for prefix in _allowed_prefixes
        ):
            raise PermissionError(f"路径不在允许范围内：{path_str}")
        return resolved

    class _ScopedOS:
        """受限 os 模块"""

        # ── os.path 完整暴露（纯计算，无副作用）──
        path = _real_os.path
        sep = _real_os.sep
        linesep = _real_os.linesep
        curdir = _real_os.curdir
        pardir = _real_os.pardir

        # ── 只读操作（安全检查后放行）──

        def listdir(self, path: str = ".") -> list:
            return _real_os.listdir(_check_path(path))

        def scandir(self, path: str = "."):
            return _real_os.scandir(_check_path(path))

        def walk(self, top: str = ".", **kwargs):
            return _real_os.walk(_check_path(top), **kwargs)

        def stat(self, path: str):
            return _real_os.stat(_check_path(path))

        def path_exists(self, path: str) -> bool:
            try:
                _check_path(path)
                return _real_os.path.exists(
                    _real_os.path.realpath(
                        _real_os.path.join(workspace_dir, path)
                        if not _real_os.path.isabs(path) else path
                    )
                )
            except PermissionError:
                return False

        def getcwd(self) -> str:
            return workspace_dir

        # ── 写操作（限制在 workspace 内）──

        def makedirs(self, path: str, exist_ok: bool = True):
            resolved = _check_path(path)
            _real_os.makedirs(resolved, exist_ok=exist_ok)

        def rename(self, src: str, dst: str):
            resolved_src = _check_path(src)
            resolved_dst = _check_path(dst)
            _real_os.rename(resolved_src, resolved_dst)

        # ── 删除操作（拦截，返回提示）──

        def remove(self, path: str):
            raise PermissionError(
                f"删除操作需要用户确认，请改用 ask_user 告知用户你要删除 {path}"
            )

        def rmdir(self, path: str):
            raise PermissionError(
                f"删除目录需要用户确认，请改用 ask_user 告知用户你要删除 {path}"
            )

        def unlink(self, path: str):
            return self.remove(path)

        # ── 环境变量（安全屏蔽）──

        environ = {}  # 空 dict，不暴露任何环境变量

        def getenv(self, key: str, default=None):
            return default  # 永远返回 default

        # ── 以下属性不存在（AttributeError）──
        # system, popen, exec, execv, execve, fork, kill, spawn*
        # 不定义 = 访问时 AttributeError，比 None 更安全

    return _ScopedOS()
```

### 3.3 shutil 受限模块

```python
# services/sandbox/scoped_os.py（续）

def build_scoped_shutil(check_path_fn):
    """受限 shutil — 只允许 copy/move，禁止 rmtree"""
    import shutil as _real_shutil

    class _ScopedShutil:
        def copy(self, src: str, dst: str):
            return _real_shutil.copy(check_path_fn(src), check_path_fn(dst))

        def copy2(self, src: str, dst: str):
            return _real_shutil.copy2(check_path_fn(src), check_path_fn(dst))

        def move(self, src: str, dst: str):
            return _real_shutil.move(check_path_fn(src), check_path_fn(dst))

        def rmtree(self, path: str):
            raise PermissionError(
                f"递归删除目录需要用户确认，请改用 ask_user"
            )

    return _ScopedShutil()
```

### 3.4 注入点：_build_sandbox_globals

```python
# sandbox_worker.py — _build_sandbox_globals 改造

def _build_sandbox_globals(workspace_dir, staging_dir, output_dir):
    # ... 现有逻辑 ...

    # ── 新增：受限 os + shutil ──
    from services.sandbox.scoped_os import build_scoped_os, build_scoped_shutil
    scoped_os = build_scoped_os(workspace_dir, staging_dir, output_dir)
    g["os"] = scoped_os
    g["shutil"] = build_scoped_shutil(scoped_os._check_path)

    # os.path 也单独暴露（方便 from os.path import join 的写法）
    # 注意：import os.path 走 restricted_import，需要在白名单中加 "os"
    
    return g
```

### 3.5 AST 校验 + import 白名单调整

```python
# validators.py — _BLOCKED_MODULES 移除 os 和 shutil
_BLOCKED_MODULES = frozenset({
    # "os",       ← 移除（用 scoped_os 替代）
    "sys",        # 保持禁止（sys.modules 绕过 import 限制）
    "subprocess", # 保持禁止（执行系统命令）
    # "shutil",   ← 移除（用 scoped_shutil 替代）
    "socket", "http", "urllib", "requests", "httpx",
    "ctypes", "importlib", "code", "codeop", "compileall",
    "multiprocessing", "threading", "signal", "resource",
    "pickle", "shelve", "marshal", "tempfile", "glob",
    ...
})

# sandbox_constants.py — ALLOWED_IMPORT_MODULES 新增
ALLOWED_IMPORT_MODULES = frozenset({
    ...,
    "os",       # 新增：import os 返回 scoped_os 实例
    "os.path",  # 新增：import os.path 返回真实 os.path（纯计算）
    "shutil",   # 新增：import shutil 返回 scoped_shutil 实例
})
```

### 3.6 restricted_import 拦截逻辑

```python
# sandbox_worker.py — restricted_import 改造

def _make_restricted_import(sandbox_globals):
    """构建受限 import 函数 — os/shutil 返回 scoped 版本"""

    def restricted_import(name, *args, **kwargs):
        if name == "os" or name == "os.path":
            return sandbox_globals["os"]  # 返回 scoped_os 实例
        if name == "shutil":
            return sandbox_globals["shutil"]  # 返回 scoped_shutil
        if name not in ALLOWED_IMPORT_MODULES:
            raise ImportError(f"禁止导入模块: {name}")
        return __import__(name, *args, **kwargs)

    return restricted_import
```

### 3.7 kernel_worker 同步改造

kernel_worker 和 sandbox_worker 使用相同的 `_build_sandbox_globals`，改造自动生效。
`_reset_security()` 需确保每次执行后 `sandbox_globals["os"]` 不被用户代码替换：

```python
# kernel_worker.py — _reset_security 补充
def _reset_security(sandbox_globals, scoped_os, scoped_shutil):
    sandbox_globals["os"] = scoped_os        # 重置
    sandbox_globals["shutil"] = scoped_shutil  # 重置
    # ... 现有的 builtins.open 重置 ...
```

---

## 4. 工具精简

### 4.1 删除的工具

| 工具 | 替代方案 | 理由 |
|------|---------|------|
| `file_list` | `os.listdir()` + `os.walk()` 在 code_execute 内 | 功能完全等价 |
| `file_search` | `os.walk()` + fnmatch 在 code_execute 内 | 功能完全等价 |
| `file_info` | `os.stat()` 在 code_execute 内 | 从未实际实现过（只有注册无逻辑） |

### 4.2 保留的工具

| 工具 | 保留原因 |
|------|---------|
| `file_read` | **PDF 文本提取 + 图片多模态返回** — os.open 读 PDF 只拿到字节流，不做文本抽取；图片需要 base64/CDN URL 注入多模态 block |
| `file_write` | 快捷创建文本文件（不写代码时） |
| `file_edit` | 精确文本替换（类似 sed，不需要写完整代码） |
| `data_query` | **DuckDB SQL 引擎** — 大数据聚合比 pandas 快 10x + 内存恒定；导出 xlsx 免内存 |
| `code_execute` | 核心执行引擎（增强后覆盖 file_list/search/info） |

### 4.3 TOOL_SYSTEM_PROMPT 更新

删除 file_list/file_search 的描述，在 code_execute 描述中新增：

```
### code_execute — 计算、文件操作与生成

沙盒内可用 os 模块操作文件：
- os.listdir(WORKSPACE_DIR) — 列出工作区文件
- os.walk(WORKSPACE_DIR) — 递归遍历
- os.stat(path) — 获取文件大小/修改时间
- os.path.join/exists/basename — 路径计算
- shutil.copy(src, dst) — 复制文件

文件访问范围限制在用户工作区内（WORKSPACE_DIR + STAGING_DIR + OUTPUT_DIR）。
删除操作被禁止，如需删除请用 ask_user 告知用户。
```

---

## 5. 删除操作的确认机制

### 5.1 设计

当 LLM 生成的代码包含删除操作时，沙盒**不在 AST 阶段拦截**——而是在运行时 `_ScopedOS.remove()` 抛出 `PermissionError`，code_execute 返回错误信息。LLM 看到后应调 `ask_user` 向用户确认。

为什么不用 `_request_user_confirm` 机制（现有 WS 确认）：
- code_execute 在**子进程**中运行，无法直接调 WS 确认
- 且删除操作在代码中间位置，无法"暂停代码执行等待确认"

**设计选择**：运行时报错 → LLM 感知 → 调 ask_user → 用户确认 → LLM 再次调 code_execute 带确认标志。

### 5.2 确认流程

```
轮次1: code_execute(code="os.remove('旧文件.xlsx')")
  → PermissionError: "删除操作需要用户确认"
  → AgentResult(status="error", metadata={"needs_confirm": "delete", "path": "旧文件.xlsx"})

轮次2: LLM 调 ask_user("需要删除文件'旧文件.xlsx'，确认吗？")
  → 用户回复"确认"

轮次3: code_execute(code="...", confirm_delete=["旧文件.xlsx"])
  → _ScopedOS.remove 检查 confirm_delete 列表 → 放行
```

### 5.3 confirm_delete 参数

code_execute 工具新增可选参数 `confirm_delete: list[str]`：

```python
# code_tools.py — code_execute 参数扩展
CODE_TOOL_SCHEMAS = {
    "code_execute": {
        "required": ["code", "description"],
        "properties": {
            "code": {"type": "string"},
            "description": {"type": "string"},
            "confirm_delete": {
                "type": "array",
                "items": {"type": "string"},
                "description": "用户已确认可删除的文件路径列表",
            },
        },
    },
}
```

---

## 6. 安全层全景

### 6.1 改造前后对比

| 安全层 | 改造前 | 改造后 |
|--------|--------|--------|
| L1 AST 校验 | 禁止 `import os` / `import shutil` | 允许（走 restricted_import 返回 scoped 版本） |
| L2 模块黑名单 | os/shutil 在黑名单 | 移出（sys/subprocess 等保持） |
| L3 restricted_import | os → ImportError | os → 返回 _ScopedOS 实例 |
| L4 运行时路径校验 | 只在 builtins.open | **扩展到 os.listdir/stat/walk/makedirs/rename** |
| L5 删除拦截 | 不存在（os 被禁了） | **新增：remove/rmdir/unlink → PermissionError** |
| L6 系统命令 | os 被禁所以不可能 | **_ScopedOS 不定义 system/popen/exec*（AttributeError）** |
| L7 环境变量 | 清理 + os 被禁 | **清理 + os.environ=空 dict + os.getenv→default** |

### 6.2 攻击面分析

| 攻击方式 | 防御 |
|---------|------|
| `os.system("rm -rf /")` | _ScopedOS 不定义 system 属性 → AttributeError |
| `os.environ["OPENAI_API_KEY"]` | os.environ = {} → KeyError |
| `os.listdir("/etc")` | _check_path → PermissionError（不在白名单） |
| `os.path.realpath("/etc") + os.listdir(...)` | os.path 是纯计算无副作用；listdir 仍经过 _check_path |
| `os.remove("重要文件.xlsx")` | PermissionError + 需 confirm_delete 参数 |
| `getattr(os, '_real_os')` | getattr 在 _BLOCKED_CALLS 中 → AST 拦截 |
| `os.__class__.__bases__` | dunder 过滤 → AST 拦截 |
| `import os; os = __import__('os')` | `__import__` 在 _BLOCKED_CALLS → AST 拦截 |
| 符号链接逃逸 `os.listdir("link_to_etc")` | _check_path 使用 realpath 解析后校验 |

---

## 7. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| 新增 scoped_os.py | `services/sandbox/` | 新文件 |
| _build_sandbox_globals 注入 os/shutil | `sandbox_worker.py:136-214` | 新增 scoped_os 注入 |
| restricted_import 允许 os | `sandbox_worker.py` | import 拦截逻辑 |
| _BLOCKED_MODULES 移除 os/shutil | `validators.py:12-20` | 黑名单修改 |
| ALLOWED_IMPORT_MODULES 新增 os/shutil | `sandbox_constants.py` | 白名单修改 |
| kernel_worker _reset_security | `kernel_manager.py` 或 `kernel_worker.py` | 重置 scoped_os |
| code_execute 新增 confirm_delete 参数 | `config/code_tools.py` | schema 扩展 |
| SandboxExecutor 传递 confirm_delete | `sandbox/executor.py` | 参数透传 |
| TOOL_SYSTEM_PROMPT 更新 | `config/chat_tools.py` | 工具描述变更 |
| 删除 file_list/file_search/file_info 注册 | `config/file_tools.py` + `config/chat_tools.py` | 工具移除 |
| tool_executor 删除 _file_list/_file_search | `tool_executor.py` | 代码删除 |
| _CONCURRENT_SAFE_TOOLS 移除 file_list/search/info | `config/chat_tools.py` | 并发标记清理 |

---

## 8. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| LLM 写 `import os; os.system("cmd")` | _ScopedOS 不定义 system → AttributeError → 正常错误返回 | scoped_os |
| LLM 写 `os.remove("file.xlsx")` 无确认 | PermissionError 提示需确认 → LLM 调 ask_user | scoped_os |
| LLM 写 `os.listdir("/root")` | _check_path → PermissionError | scoped_os |
| LLM 写 `os.walk(".")` 遍历大目录（1000+ 文件） | walk 本身 lazy generator 无内存问题；stdout 截断由 truncate_result 保证 | sandbox_worker |
| 用户 workspace 为空 | os.listdir 返回 [] — 和 file_list 行为一致 | scoped_os |
| 相对路径 `os.listdir("staging/xxx")` | _check_path 自动 join workspace_dir → 解析为绝对路径 | scoped_os |
| kernel 模式下用户篡改 `os = None` | _reset_security 每次执行重置 sandbox_globals["os"] | kernel_worker |
| confirm_delete 参数伪造 | LLM 可能编造路径放入 confirm_delete → 只允许删除 OUTPUT_DIR 内的文件 | scoped_os |

---

## 9. 任务拆分

### Phase 1：scoped_os 核心实现 + 注入

| 步骤 | 内容 | 文件 |
|------|------|------|
| 1.1 | 新建 `services/sandbox/scoped_os.py`（_ScopedOS + build_scoped_os + build_scoped_shutil） | 新文件 ~120 行 |
| 1.2 | `sandbox_worker.py` — _build_sandbox_globals 注入 scoped_os | ~10 行改 |
| 1.3 | `sandbox_worker.py` — restricted_import 允许 os 返回 scoped 实例 | ~10 行改 |
| 1.4 | `validators.py` — _BLOCKED_MODULES 移除 os/shutil | ~2 行改 |
| 1.5 | `sandbox_constants.py` — ALLOWED_IMPORT_MODULES 新增 os/os.path/shutil | ~3 行改 |
| 1.6 | 测试：os.listdir/walk/stat 放行 + os.system/environ 拦截 + 路径越界拦截 | 新文件 ~100 行 |

### Phase 2：kernel_worker 同步 + confirm_delete

| 步骤 | 内容 | 文件 |
|------|------|------|
| 2.1 | `kernel_worker.py` — _reset_security 重置 os/shutil | ~5 行改 |
| 2.2 | `config/code_tools.py` — confirm_delete 参数定义 | ~8 行改 |
| 2.3 | `sandbox/executor.py` — confirm_delete 参数透传到 worker | ~10 行改 |
| 2.4 | `scoped_os.py` — remove/unlink 检查 confirm_delete 列表 | ~15 行改 |
| 2.5 | 测试：删除流程（无确认拒绝 + 有确认放行） | ~40 行 |

### Phase 3：工具精简 + 提示词更新

| 步骤 | 内容 | 文件 |
|------|------|------|
| 3.1 | `config/file_tools.py` — 移除 file_list/file_search/file_info 定义 | ~80 行删 |
| 3.2 | `tool_executor.py` — 删除 _file_list/_file_search 方法 | ~60 行删 |
| 3.3 | `config/chat_tools.py` — TOOL_SYSTEM_PROMPT 更新 + _CONCURRENT_SAFE_TOOLS 清理 | ~20 行改 |
| 3.4 | 前端工具列表同步（如有硬编码） | 视情况 |
| 3.5 | 全量测试回归 | — |

---

## 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| scoped_os 实现有绕过漏洞 | 高 | 安全测试覆盖所有攻击向量（§6.2 全部写测试） |
| 删除 file_list 后 LLM 不会用 os.listdir | 中 | TOOL_SYSTEM_PROMPT 明确示例 + 过渡期可保留 file_list 作为快捷方式 |
| kernel 模式下 scoped_os 被篡改 | 中 | _reset_security 每次执行重置 + 测试验证 |
| file_list 的元数据自动附加能力丢失 | 中 | 在 code_execute 返回后由 executor 补充文件元数据（或保留 file_list 的元数据逻辑作为 post-hook） |
| confirm_delete 被 LLM 滥用 | 低 | 限制只能删除 OUTPUT_DIR 内文件（workspace 根目录文件不可删） |

---

## 11. 设计自检

- [x] 项目上下文已加载（双模式执行/7 层安全/工具体系/workspace 隔离）
- [x] 每个现有安全层在改造后的状态已明确（§6.1）
- [x] 攻击面分析完整（§6.2）
- [x] 保留 vs 删除工具决策有明确理由（§4）
- [x] 连锁修改全覆盖
- [x] 边界场景全覆盖
- [x] kernel_worker 同步改造未遗漏
- [x] file_read 的 PDF/图片特殊能力保留（不被 os 替代）
- [x] data_query 的 DuckDB 大数据能力保留（不被 os 替代）
- [x] auto_upload 机制不受影响（只检测 OUTPUT_DIR 快照差异）

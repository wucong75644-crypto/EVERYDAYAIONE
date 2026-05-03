# TECH: 沙盒 OS 开放与工具精简

> 版本：v3.1 | 日期：2026-05-03 | 状态：方案设计
> v1.0：骨架方案 → v2.0：补丁式补偿 → v3.0：链路本质重设计 → v3.1：15 场景验证 + 3 个问题修复

---

## 1. 运行链路全景

### 1.1 当前链路：为什么需要 4 轮工具调用？

用户说"帮我分析一下销售数据"，当前系统需要 4 轮：

```
┌─ 轮次 1 ──────────────────────────────────────────────────┐
│ 问题：LLM 不知道 workspace 有什么文件                       │
│ 解法：file_list()                                          │
│ 返回：文件名 + 元数据（📊 1000行×5列 | 读取: data_query）    │
│ 副作用：注册 FilePathCache（文件名→绝对路径映射）            │
│ Token 消耗：~300 input + ~200 result = ~500                │
└────────────────────────────────────────────────────────────┘
    ↓ LLM 看到文件列表，决定探索哪个文件
┌─ 轮次 2 ──────────────────────────────────────────────────┐
│ 问题：LLM 知道文件名但不知道列结构                          │
│ 解法：data_query(file="销售.xlsx", 无 sql)                  │
│ 返回：列名/类型/行数/统计摘要                               │
│ Token 消耗：~400 input + ~300 result = ~700                │
└────────────────────────────────────────────────────────────┘
    ↓ LLM 看到列结构，开始写 SQL 或代码
┌─ 轮次 3 ──────────────────────────────────────────────────┐
│ 问题：需要提取/聚合数据                                     │
│ 解法：data_query(sql="SELECT ...") 或 code_execute          │
│ Token 消耗：~500 input + ~400 result = ~900                │
└────────────────────────────────────────────────────────────┘
    ↓ LLM 拿到数据，做最终计算/可视化
┌─ 轮次 4 ──────────────────────────────────────────────────┐
│ 问题：计算结果/画图/生成报告                                │
│ 解法：code_execute(plt.plot / df.to_excel)                  │
│ Token 消耗：~600 input + ~500 result = ~1100               │
└────────────────────────────────────────────────────────────┘

总计：4 轮，~3200 tokens，8-12 秒延迟
每一轮的根因：LLM 需要前一步的结果才能决定下一步做什么
```

**核心矛盾**：沙盒禁止 `import os`，所以 LLM 在 code_execute 内**看不见文件系统**，必须靠独立工具一步步"发现世界"。

### 1.2 os 开放后：链路天然怎么走

```
┌─ 轮次 1 ──────────────────────────────────────────────────┐
│ LLM 一次 code_execute，代码自然包含：                       │
│                                                            │
│   import os                                                │
│   files = os.listdir('.')                                  │
│   print("文件:", files)                                    │
│                                                            │
│   # 发现 sales.xlsx → 直接读                               │
│   df = pd.read_excel('sales.xlsx')                         │
│   print(f"{df.shape[0]}行×{df.shape[1]}列")               │
│   print(f"列: {df.columns.tolist()}")                      │
│   print(df.head(3))                                        │
│                                                            │
│ 一步解决：文件发现 + 结构探索 + 数据预览                     │
│ Token 消耗：~500 input + ~400 result = ~900                │
└────────────────────────────────────────────────────────────┘
    ↓ LLM 拿到文件列表 + 数据结构 + 预览（有状态沙盒，df 保留）
┌─ 轮次 2 ──────────────────────────────────────────────────┐
│ LLM 第二次 code_execute：                                   │
│                                                            │
│   # df 上一轮已读入（有状态沙盒变量保留）                    │
│   result = df.groupby('月份')['金额'].sum()                 │
│   result.plot(kind='bar', title='月度销售趋势')             │
│   plt.savefig(OUTPUT_DIR + '/趋势图.png', dpi=150)          │
│   result.to_excel(OUTPUT_DIR + '/汇总.xlsx')                │
│   print(result)                                            │
│                                                            │
│ Token 消耗：~600 input + ~300 result = ~900                │
└────────────────────────────────────────────────────────────┘

总计：2 轮，~1800 tokens，4-6 秒延迟
比改造前节省：轮次 -50%，tokens -44%，延迟 -50%
```

**关键洞察**：os 开放后，**文件发现不再是独立步骤**——它是代码的自然行为。`os.listdir` 和 `pd.read_excel` 可以在同一段代码中顺序执行，不需要跨工具调用。

### 1.3 os 不能替代的场景

os 开放解决了"发现 → 读取 → 处理"链路，但有三个场景 os 无法替代：

| 场景 | 为什么 os 不行 | 正确工具 |
|------|-------------|---------|
| **读 PDF 提取文本** | `open('x.pdf', 'rb')` 只拿到字节流，不做 OCR/文本提取 | `file_read` — PyPDF2 提取 + 扫描件检测 |
| **看图片内容** | `open('x.png', 'rb')` 只拿字节，无法注入 LLM 多模态 | `file_read` — CDN URL / base64 注入视觉分析 |
| **大数据 SQL 聚合** | `pd.read_excel` 10万行吃 2GB 内存 | `data_query` — DuckDB 恒定 256MB + 流式导出 |

这三个不是"工具精简后需要保留"，而是**它们本来就解决不同的问题**。os 和这三个工具各管各的。

### 1.4 file_list / file_search 的能力归属分析

基于链路思考，重新审视这两个工具的每个能力在 os 链路中的归属：

#### 元数据自动附加（行列数/类型/读取命令）

**当前**：file_list 调 `extract_file_metadata()` 对前 5 个文件提取结构信息。
**os 链路中**：LLM 在 code_execute 内直接写 `df = pd.read_excel(f); print(df.shape, df.columns.tolist())`——**比元数据更准确**（元数据用 500 行采样推断类型，pandas 用全量数据）。
**结论**：这个能力被 code_execute 的自然代码行为**替代且超越**，不需要补偿。

#### FilePathCache（文件名→路径映射 + 去空格容错）

**当前**：file_list 注册 → data_query/file_read 解析模糊文件名。
**os 链路中**：LLM 在 code_execute 内用**变量**传递文件名（`for f in os.listdir('.'): df = pd.read_excel(f)`），不存在"LLM 重新生成文件名加空格"的问题——**变量传递天然精确**。
**跨工具场景**：code_execute → data_query 的跨工具调用仍需缓存。但这只发生在大文件（>10万行）需要 DuckDB 时。此时 LLM 已经在 code_execute 中拿到了精确文件名，可以通过 print 传递。
**结论**：os 链路下 FilePathCache 的核心价值大幅降低。保留现有机制作为兜底即可，不需要额外补偿。

#### 敏感文件过滤（.env/.git 不显示）

**当前**：file_list 的 `_BLOCKED_NAMES` 过滤。
**os 链路中**：workspace 是用户隔离目录（`/org/{org_id}/{user_id}/`），**本来就没有 .env/.git**。这些文件在后端代码目录，不在用户 workspace。scoped_open 的路径白名单是更底层的安全保障。
**结论**：**不需要在 scoped_os.listdir 中做额外过滤**。如果出现意外文件，scoped_open 拒绝打开即可。

#### 内容搜索（search_content=true）

**当前**：file_search 逐行匹配文件内容。
**os 链路中**：
```python
import os
for root, dirs, files in os.walk('.'):
    for f in files:
        path = os.path.join(root, f)
        try:
            text = open(path).read()
            if '关键词' in text:
                print(f"找到: {path}")
        except: pass
```
3 行代码，和 file_search 完全等价。
**结论**：被 code_execute 自然替代。

#### 格式化输出（📊 emoji + 对齐排列）

**当前**：file_list 精心格式化的输出让 LLM 一眼理解。
**os 链路中**：LLM 自己写 `print(f"{f}: {os.stat(f).st_size} bytes")`，格式随代码控制。
**结论**：不需要补偿。LLM 本来就擅长格式化输出。

---

## 2. os 模块的能力范围与边界

### 2.1 scoped_os 提供什么

| 操作 | API | 安全机制 | 用途 |
|------|-----|---------|------|
| 列出目录 | `os.listdir(path)` | _check_path 白名单校验 | 发现文件 |
| 递归遍历 | `os.walk(top)` | _check_path 白名单校验 | 搜索文件 |
| 文件信息 | `os.stat(path)` | _check_path 白名单校验 | 文件大小/修改时间 |
| 路径计算 | `os.path.*` | 纯计算无副作用 | join/exists/basename/splitext |
| 路径常量 | `os.sep`/`os.linesep` | 只读 | 跨平台兼容 |
| 创建目录 | `os.makedirs(path)` | _check_path + 白名单内 | 组织输出文件 |
| 重命名 | `os.rename(src, dst)` | _check_path 双端校验 | 文件管理 |
| 当前目录 | `os.getcwd()` | 返回 workspace_dir | 路径基准 |

### 2.2 scoped_os 不提供什么

| 操作 | 为什么不提供 | 攻击面 |
|------|------------|--------|
| `os.system` / `os.popen` | 执行系统命令 | 属性不存在 → AttributeError |
| `os.exec*` / `os.fork` / `os.kill` | 进程操作 | 属性不存在 → AttributeError |
| `os.environ` | 环境变量泄露 | 返回空 dict `{}` |
| `os.getenv` | 同上 | 永远返回 default |
| `os.remove` / `os.unlink` | 删除文件 | PermissionError + 引导 ask_user |
| `os.rmdir` | 删除目录 | PermissionError |

### 2.3 路径安全边界

```
允许访问的路径（_allowed_prefixes）：
├─ workspace_dir:  /workspace/org/{org_id}/{user_id}/       用户工作区
├─ staging_dir:    /workspace/org/{org_id}/{user_id}/staging/{conv_id}/  临时数据
└─ output_dir:     /workspace/org/{org_id}/{user_id}/下载/   输出目录

禁止访问的路径（一切不在上述前缀下的路径）：
├─ /etc/*          → PermissionError
├─ /root/*         → PermissionError
├─ /tmp/*          → PermissionError（不再放行 /tmp）
├─ 其他用户目录     → PermissionError
└─ 符号链接逃逸     → realpath 解析后校验，无法绕过
```

### 2.4 与现有安全层的协作

scoped_os 不是替代现有安全层，而是**新增一层**：

```
用户代码: import os; os.listdir('/etc')
    ↓
L1 AST 校验: import os → 允许（移出黑名单）✓
    ↓
L2 restricted_import: os → 返回 _ScopedOS 实例（不是真实 os）✓
    ↓
L3 运行时: _ScopedOS.listdir('/etc') → _check_path → PermissionError ✗
    ✓ 拦截成功

用户代码: os.system('rm -rf /')
    ↓
L1 AST 校验: 允许 ✓
    ↓
L2 restricted_import: os → _ScopedOS ✓
    ↓
L3 运行时: _ScopedOS 没有 system 属性 → AttributeError ✗
    ✓ 拦截成功

用户代码: open('/etc/passwd')
    ↓
L4 builtins.open = scoped_open → _check_path → PermissionError ✗
    ✓ 拦截成功（现有机制不变）
```

---

## 3. 工具精简决策

### 3.1 决策矩阵

| 工具 | 决策 | 理由 |
|------|------|------|
| **file_list** | **删除** | os.listdir + pd.read 在 code_execute 内完全替代。元数据通过代码获取更准确 |
| **file_search** | **删除** | os.walk + 文件名匹配 / 内容搜索在 code_execute 内 3 行代码替代 |
| **file_info** | **删除** | 从未实际实现过（只有注册无逻辑）。os.stat 替代 |
| **file_read** | **保留** | PDF 文本提取 + 图片多模态返回是不可替代能力 |
| **file_write** | **保留** | 快捷文本创建（不需要写代码时）|
| **file_edit** | **保留** | 精确文本替换（类似 sed）|
| **data_query** | **保留** | DuckDB SQL 引擎，大数据聚合不可替代 |
| **code_execute** | **增强** | 新增 os/shutil 能力，成为文件操作的主力 |

### 3.2 FilePathCache 处理策略

**不删除 workspace_file_handles.py**，但改变注册来源：

当前：file_list / file_search 执行时注册
改造后：
- **code_execute 后处理自动注册**：解析 stdout 中出现的文件名，匹配 workspace 内实际文件后注册
- **data_query 执行时自注册**：data_query 解析到文件时自行注册（已有此逻辑）
- **file_read 执行时自注册**：同上

```python
# tool_executor.py — code_execute 后处理新增
async def _code_execute(self, args):
    result = await self._sandbox.execute(code=args["code"], ...)
    
    # 从 stdout 中提取被操作的文件名，注册到缓存
    self._register_files_from_output(result.summary)
    
    return result

def _register_files_from_output(self, stdout: str):
    """从 code_execute 输出中提取文件名并注册到路径缓存
    
    匹配模式：
    1. os.listdir 输出的列表格式: ['file1.xlsx', 'file2.csv']
    2. pd.read_excel/read_csv 的参数: pd.read_excel('file.xlsx')
    3. open() 的参数: open('file.txt')
    """
    import re
    from services.agent.workspace_file_handles import get_file_cache
    file_cache = get_file_cache(self.conversation_id)
    
    # 提取文件名（常见数据文件扩展名）
    _FILE_PATTERN = re.compile(
        r"['\"]([^'\"]*\.(?:xlsx|xls|csv|tsv|parquet|pdf|docx|pptx|txt|json|png|jpg))['\"]",
        re.IGNORECASE,
    )
    for m in _FILE_PATTERN.finditer(stdout):
        filename = m.group(1)
        abs_path = self._resolve_in_workspace(filename)
        if abs_path:
            file_cache.register(os.path.basename(filename), abs_path)
```

这样：
- LLM 在 code_execute 内 `os.listdir` 发现的文件名 → 自动注册
- 后续 data_query/file_read 的模糊匹配继续工作
- 不需要 file_list 作为注册入口

### 3.3 元数据提取的归属

`file_metadata_extractor.py` 不删除，但改变使用方式：

当前：file_list 调 `extract_file_metadata()` 在工具返回中附加
改造后：**不主动调用**。LLM 在 code_execute 内自然获取更准确的结构信息：
- `df.shape` → 精确行列数（不是采样估算）
- `df.dtypes` → 精确类型（不是推断）
- `df.head()` → 真实预览（不是格式化摘要）

`file_metadata_extractor.py` 保留供 file_read 使用（PDF 元数据等）。

---

## 4. scoped_os 实现

### 4.1 核心实现

```python
# services/sandbox/scoped_os.py（新文件）

"""沙盒受限 os 模块 — 只暴露安全文件操作，屏蔽系统命令和进程操作"""

import os as _real_os


def build_scoped_os(workspace_dir: str, staging_dir: str, output_dir: str):
    """构建受限 os 模块实例

    每次执行构造一份，confirm_delete 通过 set_confirmed_deletes 注入。
    """

    _allowed_prefixes = [
        _real_os.path.realpath(workspace_dir),
    ]
    if staging_dir:
        _allowed_prefixes.append(_real_os.path.realpath(staging_dir))
    if output_dir:
        _allowed_prefixes.append(_real_os.path.realpath(output_dir))

    _confirmed_deletes: list[str] = []

    def _check_path(path_str) -> str:
        """路径安全校验 — 解析相对路径 + realpath + 白名单"""
        path_str = str(path_str)
        if not _real_os.path.isabs(path_str):
            path_str = _real_os.path.join(workspace_dir, path_str)
        resolved = _real_os.path.realpath(path_str)
        if not any(
            resolved == prefix or resolved.startswith(prefix + _real_os.sep)
            for prefix in _allowed_prefixes
        ):
            raise PermissionError(f"路径不在允许范围内: {path_str}")
        return resolved

    def set_confirmed_deletes(paths: list[str]) -> None:
        """设置本次执行允许删除的文件列表"""
        _confirmed_deletes.clear()
        for p in paths:
            _confirmed_deletes.append(_check_path(p))

    class _ScopedOS:
        """受限 os 模块 — 对外行为像 os，内部所有 IO 操作经过路径校验"""

        # os.path 完整暴露（纯计算，无副作用）
        path = _real_os.path
        sep = _real_os.sep
        linesep = _real_os.linesep
        curdir = _real_os.curdir
        pardir = _real_os.pardir

        # 只读操作
        # 设计原则：
        #   listdir/stat — 返回值不含路径，传绝对路径无影响
        #   walk/scandir — 返回值的 root/entry.path 跟随传入路径格式
        #                  必须传原始路径（保持相对），否则 stdout 泄露绝对路径
        #                  安全检查和实际调用分开

        @staticmethod
        def listdir(path="."):
            return _real_os.listdir(_check_path(path))

        @staticmethod
        def scandir(path="."):
            _check_path(path)                     # 安全检查
            return _real_os.scandir(path)          # 传原始路径

        @staticmethod
        def walk(top=".", **kwargs):
            _check_path(top)                       # 安全检查
            return _real_os.walk(top, **kwargs)    # 传原始路径

        @staticmethod
        def stat(path):
            return _real_os.stat(_check_path(path))

        @staticmethod
        def getcwd():
            return workspace_dir

        # 写操作（限制在 workspace 内）

        @staticmethod
        def makedirs(path, exist_ok=True):
            _real_os.makedirs(_check_path(path), exist_ok=exist_ok)

        @staticmethod
        def rename(src, dst):
            _real_os.rename(_check_path(src), _check_path(dst))

        # 删除操作（需 confirm_delete 参数）

        @staticmethod
        def remove(path):
            resolved = _check_path(path)
            if resolved in _confirmed_deletes:
                return _real_os.remove(resolved)
            name = _real_os.path.basename(path)
            raise PermissionError(
                f"删除操作需要用户确认。请先调 ask_user 告知用户要删除 {name}，"
                f"确认后在 code_execute 的 confirm_delete 参数传入文件名。"
            )

        @staticmethod
        def rmdir(path):
            raise PermissionError("删除目录被禁止。")

        unlink = remove

        # 环境变量屏蔽

        environ = {}

        @staticmethod
        def getenv(key, default=None):
            return default

        # system/popen/exec*/fork/kill → 不定义 → AttributeError

    scoped = _ScopedOS()
    scoped._set_confirmed_deletes = set_confirmed_deletes
    return scoped, _check_path


def build_scoped_shutil(check_path_fn):
    """受限 shutil — copy/move 放行，rmtree 禁止"""
    import shutil as _real_shutil

    class _ScopedShutil:
        @staticmethod
        def copy(src, dst):
            return _real_shutil.copy(check_path_fn(src), check_path_fn(dst))

        @staticmethod
        def copy2(src, dst):
            return _real_shutil.copy2(check_path_fn(src), check_path_fn(dst))

        @staticmethod
        def move(src, dst):
            return _real_shutil.move(check_path_fn(src), check_path_fn(dst))

        @staticmethod
        def rmtree(path):
            raise PermissionError("递归删除目录被禁止。")

    return _ScopedShutil()
```

### 4.2 restricted_import 改造

**问题**：当前 `restricted_import` 是模块级全局函数（`sandbox_constants.py:46-54`），return `__import__`。改造后需要能拦截 `import os` 返回 scoped 实例。

**方案**：改为闭包工厂，保持向后兼容。

```python
# sandbox_constants.py — 新增

def make_restricted_import(scoped_modules=None):
    """构建受限 import 函数
    
    Args:
        scoped_modules: {"os": scoped_os, "shutil": scoped_shutil}
                        None 时退化为原始行为
    """
    _scoped = scoped_modules or {}

    def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        top = name.split(".")[0]
        if top in _scoped:
            mod = _scoped[top]
            if name == "os.path":
                return mod.path  # os.path 返回真实 os.path（纯计算）
            return mod
        if top not in ALLOWED_IMPORT_MODULES:
            raise ImportError(f"禁止导入模块: {name}")
        return __import__(name, globals, locals, fromlist, level)

    return _restricted_import

# 向后兼容
restricted_import = make_restricted_import()
```

### 4.3 注入到沙盒执行环境

```python
# sandbox_worker.py — _build_sandbox_globals 改造

def _build_sandbox_globals(workspace_dir, staging_dir, output_dir):
    from services.sandbox.scoped_os import build_scoped_os, build_scoped_shutil
    from services.sandbox.sandbox_constants import make_restricted_import

    scoped_os, check_path = build_scoped_os(workspace_dir, staging_dir, output_dir)
    scoped_shutil = build_scoped_shutil(check_path)

    scoped_import = make_restricted_import({"os": scoped_os, "shutil": scoped_shutil})

    safe_builtins = SAFE_BUILTINS.copy()
    safe_builtins["__import__"] = scoped_import

    g = {"__builtins__": safe_builtins}
    g["os"] = scoped_os
    g["shutil"] = scoped_shutil

    # ... 现有逻辑（math/json/datetime/pd/plt/Path 等）不变 ...
    return g
```

### 4.4 AST 校验调整

```python
# validators.py
_BLOCKED_MODULES = frozenset({
    # "os",       ← 移除
    "sys",
    "subprocess",
    # "shutil",   ← 移除
    "socket", "http", "urllib", "requests", "httpx",
    "ctypes", "importlib", "code", "codeop", "compileall",
    "multiprocessing", "threading", "signal", "resource",
    "pickle", "shelve", "marshal", "tempfile", "glob",
    "webbrowser", "ftplib", "smtplib", "telnetlib",
    "builtins", "__builtin__",
})
```

```python
# sandbox_constants.py — ALLOWED_IMPORT_MODULES 新增
ALLOWED_IMPORT_MODULES = frozenset({
    ...,  # 现有 43 个模块
    "os",       # import os → restricted_import 返回 scoped_os
    "os.path",  # import os.path → 返回真实 os.path
    "shutil",   # import shutil → restricted_import 返回 scoped_shutil
})
```

### 4.5 kernel_worker 同步

```python
# kernel_worker.py — _reset_security 扩展

def _reset_security(sandbox_globals, scoped_open, scoped_os, scoped_shutil, scoped_import):
    """每次执行前重置安全关键项（防跨调用篡改）"""
    import builtins

    safe = SAFE_BUILTINS.copy()
    safe["__import__"] = scoped_import
    sandbox_globals["__builtins__"] = safe

    builtins.open = scoped_open
    sandbox_globals["open"] = scoped_open
    sandbox_globals["os"] = scoped_os
    sandbox_globals["shutil"] = scoped_shutil
```

---

## 5. confirm_delete 全链路

### 5.1 为什么需要三轮确认

沙盒在子进程中运行，无法"暂停代码执行等待用户确认"。所以：

```
轮次1: code_execute(code="os.remove('旧报表.xlsx')")
  → scoped_os.remove() → PermissionError:
    "删除操作需要用户确认。请先调 ask_user 告知用户要删除 旧报表.xlsx，
     确认后在 code_execute 的 confirm_delete 参数传入文件名。"
  → LLM 看到错误

轮次2: ask_user(message="需要删除「旧报表.xlsx」吗？")
  → 用户回复"好的"

轮次3: code_execute(code="os.remove('旧报表.xlsx')", confirm_delete=["旧报表.xlsx"])
  → scoped_os.remove() → _confirmed_deletes 命中 → 删除成功
```

### 5.2 传递链路

```
code_tools.py schema 定义 confirm_delete: list[str]
    ↓
ToolExecutor._code_execute(args)
    提取 args.get("confirm_delete", [])
    ↓
SandboxExecutor.execute(code, confirm_delete=["旧报表.xlsx"])
    ├─ Stateless: Queue.put({"code": ..., "confirm_delete": [...]})
    │   → sandbox_worker: scoped_os._set_confirmed_deletes(confirm_delete)
    │   → exec(code) → os.remove() 检查 _confirmed_deletes → 放行
    └─ Stateful:  stdin.write(json({"code": ..., "confirm_delete": [...]}))
        → kernel_worker: scoped_os._set_confirmed_deletes(confirm_delete)
        → exec(code) → os.remove() 检查 _confirmed_deletes → 放行
```

每次执行前 `_set_confirmed_deletes` 重新设置，执行后自动清空——**不跨调用残留**。

---

## 6. 提示词改造

### 6.1 TOOL_SYSTEM_PROMPT 改造

**删除**（`chat_tools.py:242-243`）：
```
### file_list / file_search — 工作区文件发现
查看工作区有哪些文件、搜索特定文件。Excel/CSV/Parquet 等数据文件用 data_query 查询，不能用 file_read。
```

**code_execute 段落改造**（`chat_tools.py:181-213`）：

```
### code_execute — Python 计算环境

有状态沙盒：同一对话内变量跨调用保留。

何时使用：
- 浏览工作区文件、了解数据结构（os.listdir + pd.read + df.shape）
- 对数据做计算、统计、可视化、格式转换
- 涉及多个文件的对比、匹配、合并、JOIN
- 数据清洗（去空格、统一格式、模糊匹配）
- 搜索文件（os.walk + 文件名/内容匹配）

不适用：
- 读 PDF/图片内容 → file_read（自动提取文本/视觉分析）
- 从超过 10 万行的大文件中聚合筛选 → data_query（DuckDB 恒定内存）
- 查询 ERP 业务数据 → erp_agent

核心能力：
- 可用库：pd, plt, Path, math, json, datetime, Decimal, Counter, io
- os 模块：os.listdir / os.walk / os.stat / os.path.*（路径限制在工作区内）
- shutil 模块：shutil.copy / shutil.move
- 变量在对话期间持续存在
- 最终给用户的文件写到 OUTPUT_DIR，平台自动检测上传
- 中间计算结果写到 STAGING_DIR

典型用法——一步到位发现文件并分析：
  import os
  files = os.listdir('.')
  print("工作区文件:", files)
  for f in files:
      if f.endswith(('.xlsx', '.csv')):
          df = pd.read_excel(f) if f.endswith('.xlsx') else pd.read_csv(f)
          print(f"\n{f}: {df.shape[0]}行×{df.shape[1]}列, 列: {df.columns.tolist()}")

注意事项：
- os 只能访问工作区内的文件，越界报 PermissionError
- 删除操作需先用 ask_user 确认，确认后在 confirm_delete 参数传入文件名
- 禁止 import sys/subprocess
```

### 6.2 FILE_ROUTING_PROMPT 改造

```python
FILE_ROUTING_PROMPT = (
    "## 文件操作规则\n"
    "- 所有文件操作直接用文件名或相对路径（如 '利润表.xlsx'、'子目录/data.csv'）\n"
    "- 浏览目录 + 读数据 + 分析 → code_execute（os.listdir + pd.read，一步到位）\n"
    "- 读取 PDF/图片 → file_read（自动提取文本/视觉分析）\n"
    "- 查询/聚合大数据文件（>10万行）→ data_query（DuckDB 恒定内存）\n"
    "- 写入/创建文件 → file_write（快捷文本创建）或 code_execute（生成 Excel/图表）\n"
    "- 精确替换文本 → file_edit\n"
)
```

### 6.3 code_execute description 扩展

在 `_DESCRIPTION_WORKSPACE` 末尾追加：

```
沙盒可用 os 模块操作文件：
- os.listdir('.') — 列出当前目录
- os.walk('.') — 递归遍历
- os.stat(path) — 文件大小/修改时间
- os.path.join/exists/basename/splitext — 路径计算
- shutil.copy(src, dst) / shutil.move(src, dst) — 复制/移动文件
路径限制在工作区内，越界报 PermissionError。
```

---

## 7. 安全层全景

### 7.1 改造前后对比

| 安全层 | 改造前 | 改造后 |
|--------|--------|--------|
| L1 AST | `import os` → 拦截 | `import os` → 放行 |
| L2 模块黑名单 | os/shutil 在黑名单 | 移出（sys/subprocess 等保持） |
| L3 restricted_import | os → ImportError | os → 返回 _ScopedOS（闭包工厂） |
| **L4 scoped_os** | 不存在 | **新增：路径白名单 + 操作限制** |
| L5 scoped_open | 路径白名单 + 文件名纠错 | 不变（os 和 open 双重保护） |
| L6 资源限制 | 内存 2GB / CPU 限制 | 不变 |
| L7 环境变量 | 清理敏感前缀 | 不变 + os.environ={} |

### 7.2 攻击面测试矩阵

| # | 攻击代码 | 预期结果 | 防御层 |
|---|---------|---------|--------|
| 1 | `os.system("rm -rf /")` | AttributeError | L4 |
| 2 | `os.popen("cat /etc/passwd")` | AttributeError | L4 |
| 3 | `os.environ["OPENAI_API_KEY"]` | KeyError（空 dict） | L4 |
| 4 | `os.listdir("/etc")` | PermissionError | L4 |
| 5 | `os.listdir("../../")` | PermissionError（realpath 后越界） | L4 |
| 6 | `os.remove("重要文件.xlsx")` | PermissionError（需 confirm_delete） | L4 |
| 7 | `os.remove(path)` 带 confirm_delete | 成功删除 | L4 |
| 8 | `os.stat("/etc/passwd")` | PermissionError | L4 |
| 9 | `os.walk("/")` | PermissionError | L4 |
| 10 | `import os; os = __import__('os')` | AST 拦截（__import__ 在黑名单） | L1 |
| 11 | `getattr(os, '_real_os')` | AST 拦截（getattr 在黑名单） | L1 |
| 12 | `os.__class__.__bases__` | AST 拦截（dunder 黑名单） | L1 |
| 13 | `from os import system` | 返回 _ScopedOS → 无 system 属性 → AttributeError | L3+L4 |
| 14 | 符号链接逃逸 | realpath 解析后白名单校验 | L4 |
| 15 | kernel 模式 `del os` 后 import | _reset_security 重置 | L3 |

---

## 8. 连锁修改清单

| # | 改动 | 文件 | Phase | 估计 |
|---|------|------|-------|------|
| 1 | 新建 scoped_os.py | `services/sandbox/scoped_os.py` | 1 | ~130 行新文件 |
| 2 | make_restricted_import 闭包工厂 | `sandbox_constants.py` | 1 | ~20 行改 |
| 3 | ALLOWED_IMPORT_MODULES 新增 os/shutil | `sandbox_constants.py` | 1 | 3 行加 |
| 4 | _BLOCKED_MODULES 移除 os/shutil | `validators.py` | 1 | 2 行删 |
| 5 | _build_sandbox_globals 注入 | `sandbox_worker.py` | 1 | ~15 行改 |
| 6 | kernel_worker _reset_security 扩展 | `kernel_worker.py` | 2 | ~8 行改 |
| 7 | kernel_worker 接收 confirm_delete | `kernel_worker.py` | 2 | ~5 行改 |
| 8 | sandbox_worker 接收 confirm_delete | `sandbox_worker.py` | 2 | ~5 行改 |
| 9 | SandboxExecutor 透传 confirm_delete | `executor.py` | 2 | ~10 行改 |
| 10 | code_execute schema 新增 confirm_delete | `config/code_tools.py` | 2 | ~10 行改 |
| 11 | code_execute description 扩展 | `config/code_tools.py` | 3 | ~10 行改 |
| 12 | TOOL_SYSTEM_PROMPT 改造 | `config/chat_tools.py` | 3 | ~30 行改 |
| 13 | FILE_ROUTING_PROMPT 改造 | `config/file_tools.py` | 3 | ~10 行改 |
| 14 | 删除 file_list/search 工具定义 | `config/file_tools.py` | 3 | ~80 行删 |
| 15 | 删除 _file_list_with_metadata / _file_search_with_metadata | `tool_executor.py` | 3 | ~90 行删 |
| 16 | FILE_INFO_TOOLS 移除 file_list/search | `config/file_tools.py` | 3 | 2 行删 |
| 17 | _CORE_TOOLS 移除 file_list/search/info | `config/chat_tools.py` | 3 | 3 行删 |
| 18 | _CONCURRENT_SAFE_TOOLS 移除 file_list/search/info | `config/chat_tools.py` | 3 | 3 行删 |
| 19 | code_execute 后处理注册文件缓存 | `tool_executor.py` | 3 | ~30 行加 |
| 20 | tool_registry 移除 file_list/search 注册 | `config/tool_registry.py` | 3 | ~10 行删 |

**不改的**：
- `file_executor.py`（file_read/write/edit 继续使用）
- `file_metadata_extractor.py`（file_read 继续使用）
- `workspace_file_handles.py`（保留，改为 code_execute 后处理注册）
- `data_query_executor.py`（不变）
- auto_upload 机制（不变）
- [FILE] 标记流（不变）

---

## 9. 任务拆分

### Phase 1：scoped_os 核心（~2 天）

| 步骤 | 内容 |
|------|------|
| 1.1 | 新建 `scoped_os.py`：_ScopedOS + build_scoped_os + build_scoped_shutil |
| 1.2 | `sandbox_constants.py`：make_restricted_import + ALLOWED_IMPORT_MODULES |
| 1.3 | `validators.py`：_BLOCKED_MODULES 移除 os/shutil |
| 1.4 | `sandbox_worker.py`：_build_sandbox_globals 注入 |
| 1.5 | 测试：§7.2 攻击面矩阵全部 15 个用例 |

### Phase 2：confirm_delete + kernel 同步（~1.5 天）

| 步骤 | 内容 |
|------|------|
| 2.1 | `code_tools.py`：confirm_delete 参数 schema + description 扩展 |
| 2.2 | `executor.py`：SandboxExecutor 透传 confirm_delete |
| 2.3 | `sandbox_worker.py` + `kernel_worker.py`：接收 confirm_delete → set_confirmed_deletes |
| 2.4 | `kernel_worker.py`：_reset_security 扩展（重置 os/shutil/import） |
| 2.5 | 测试：删除三轮流程 + kernel 跨调用不残留 + 越界删除拒绝 |

### Phase 3：工具精简 + 提示词 + 缓存注册（~2 天）

| 步骤 | 内容 |
|------|------|
| 3.1 | `chat_tools.py`：TOOL_SYSTEM_PROMPT 改造 + _CORE_TOOLS/_CONCURRENT_SAFE 清理 |
| 3.2 | `file_tools.py`：FILE_ROUTING_PROMPT 改造 + 删除 file_list/search 定义 |
| 3.3 | `tool_executor.py`：删除 _file_list/_file_search_with_metadata + 新增 code_execute 后处理注册文件缓存 |
| 3.4 | `tool_registry.py`：移除 file_list/search 注册 |
| 3.5 | E2E 测试：code_execute(os.listdir) → 后处理注册 → data_query 模糊匹配可用 |
| 3.6 | 全量回归 |

---

## 10. 风险评估

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| scoped_os 绕过漏洞 | **高** | §7.2 全部 15 个攻击用例写测试 |
| LLM 不会用 os.listdir | **中** | 提示词含完整代码示例 + "典型用法"段落 |
| restricted_import 改造破坏兼容性 | **低** | `restricted_import = make_restricted_import()` 保持默认行为 |
| confirm_delete 序列化问题 | **低** | list[str] 是基本类型，Queue/JSON 原生支持 |
| 删除 file_list 后旧对话回放异常 | **低** | 工具结果已持久化到 DB，不影响历史消息展示 |

---

## 11. 场景验证（v3.1 新增）

### 11.1 15 场景模拟结果

| # | 场景 | 类型 | os 链路走法 | 结果 | 发现问题 |
|---|------|------|-----------|------|---------|
| 1 | 最常见：分析销售数据 | 常规 | os.listdir → pd.read_excel → df.plot | ✅ 4→2 轮 | — |
| 2 | 中文文件名含空格/括号 | 边缘 | os.listdir 返回精确名 → 变量传递 | ✅ 比 FilePathCache 更可靠 | — |
| 3 | 超大文件 2GB/500万行 | 极端 | os.stat 看大小 → pd.read_csv 截断 2000 行 → 转 data_query | ⚠️ | **问题 A** |
| 4 | Excel 3 个 Sheet | 常规 | pd.ExcelFile().sheet_names → 逐 Sheet 读 | ✅ 比当前更好 | — |
| 5 | GBK 编码 CSV | 边缘 | pd.read_csv 默认 UTF-8 失败 → LLM 重试 encoding='gbk' | ⚠️ | **问题 B** |
| 6 | 空 workspace | 边缘 | os.listdir 返回 [] → LLM 引导上传 | ✅ | — |
| 7 | 深层嵌套目录（3层） | 常规 | os.walk 一次性遍历 | ✅ 比 3 次 file_list 更好 | — |
| 8 | 混合类型（xlsx+pdf+png+docx+json） | 复杂 | os.listdir 发现 → 分别路由到 file_read/code_execute | ⚠️ | **问题 C** |
| 9 | LLM 手动输入文件名（kernel 重置后） | 边缘 | scoped_open._find_similar_file_global 纠错 | ✅ | — |
| 10 | 1200 个文件的目录 | 极端 | os.listdir 返回全部 → LLM 编程筛选 | ✅ 比 200 截断更灵活 | — |
| 11 | staging parquet（erp_agent 产出） | 常规 | pd.read_parquet(STAGING_DIR + '/xxx.parquet') | ✅ | — |
| 12 | 同名文件覆盖保护 | 边缘 | SandboxExecutor._dedup_overwritten_files 不受影响 | ✅ | — |
| 13 | 路径越界攻击 | 安全 | _check_path → realpath → 白名单 → PermissionError | ✅ | — |
| 14 | Kernel 超时变量丢失 | 边缘 | os.listdir 重新发现 → pd.read 重新读入 | ✅ 比调 file_list 更快 | — |
| 15 | 计划模式复杂多步 | 复杂 | erp_agent×3 并行 → os.listdir staging → pd.read + 对比 | ✅ 省掉 file_list 轮 | — |

### 11.2 发现的 3 个问题及修复

#### 问题 A：大文件截断无明确信号

**场景**：500 万行 CSV，PandasProxy 默认 nrows=2000 截断。LLM 看到 `df.shape = (2000, 8)`，无法区分"文件本来就 2000 行"还是"被截断了"。

**影响**：LLM 可能以为数据完整，用 2000 行样本做汇总得出错误结论。

**修复**：PandasProxy 截断时自动 print 提示。

```python
# sandbox_worker.py — _wrap_pd_reader 改造

def _wrap_pd_reader(original_fn):
    @functools.wraps(original_fn)
    def wrapper(*args, **kwargs):
        if "nrows" not in kwargs:
            kwargs["nrows"] = _DEFAULT_NROWS
            result = original_fn(*args, **kwargs)
            # 如果返回行数恰好等于 nrows，说明可能被截断
            if hasattr(result, '__len__') and len(result) >= _DEFAULT_NROWS:
                import sys
                print(
                    f"⚠️ 数据已截断到前 {_DEFAULT_NROWS} 行（文件可能更大）。"
                    f"如需全量分析，用 data_query SQL 聚合，或传 nrows=None 全读。",
                    file=sys.stderr,
                )
            return result
        elif kwargs["nrows"] is None:
            del kwargs["nrows"]
        return original_fn(*args, **kwargs)
    return wrapper
```

**效果**：LLM 看到截断提示后，自然知道用 data_query 或 nrows=None。

**连锁修改**：`sandbox_worker.py` + `kernel_worker.py`（两处 _wrap_pd_reader 同步改）

#### 问题 B：GBK 编码首次读取失败

**场景**：GBK 编码 CSV，pd.read_csv 默认 UTF-8 → UnicodeDecodeError。

**影响**：LLM 需要看到错误后重试 encoding='gbk'，多一轮调用。

**修复**：提示词引导（不改代码）。在 code_execute 注意事项中追加：

```
- 中文 CSV 读取报 UnicodeDecodeError 时，尝试 encoding='gbk' 或 encoding='gb18030'
```

**为什么不在 PandasProxy 里自动检测编码**：
- 增加代码复杂度（需要引入 chardet 依赖到沙盒）
- LLM 看到错误后重试是正常开发行为，不需要隐藏
- data_query 已有 chardet 自动检测，大文件场景用 data_query 更合适

#### 问题 C：LLM 可能在 code_execute 里读 PDF 而非调 file_read

**场景**：workspace 有 PDF 和 PNG，LLM 可能在 code_execute 里用 PyPDF2 读 PDF（沙盒允许），但这样失去了 file_read 的两个独有能力：
1. PDF 扫描件检测 + 页数校验 + 分页控制
2. 图片多模态注入（CDN URL / base64 → LLM 视觉分析）

**影响**：PDF 可用但体验下降（无分页、无扫描件提示）；图片完全不可用（code_execute 无法注入多模态 block）。

**修复**：提示词明确边界。在 code_execute 的"不适用"段落修改为：

```
不适用：
- 读 PDF 内容 → file_read（自动分页提取、扫描件检测，比 PyPDF2 更完善）
- 看图片内容 → file_read（沙盒无法进行视觉分析，必须用 file_read 注入多模态）
- 从超过 10 万行的大文件中聚合筛选 → data_query（DuckDB 恒定内存）
- 查询 ERP 业务数据 → erp_agent
```

关键补充：**"沙盒无法进行视觉分析"**——这是图片必须用 file_read 的根本原因，不是"建议"而是"不能"。

### 11.3 提示词最终版（整合 3 个问题修复）

```
### code_execute — Python 计算环境

有状态沙盒：同一对话内变量跨调用保留。

何时使用：
- 浏览工作区文件、了解数据结构（os.listdir + pd.read + df.shape）
- 对数据做计算、统计、可视化、格式转换
- 涉及多个文件的对比、匹配、合并、JOIN
- 数据清洗（去空格、统一格式、模糊匹配）
- 搜索文件（os.walk + 文件名/内容匹配）
- 读写 Word/PPT（python-docx / python-pptx）

不适用：
- 读 PDF 内容 → file_read（自动分页提取、扫描件检测）
- 看图片内容 → file_read（沙盒无法进行视觉分析，必须用 file_read）
- 从超过 10 万行的大文件中聚合筛选 → data_query（DuckDB 恒定内存）
- 查询 ERP 业务数据 → erp_agent

核心能力：
- 可用库：pd, plt, Path, math, json, datetime, Decimal, Counter, io, docx, pptx
- os 模块：os.listdir / os.walk / os.stat / os.path.*（路径限制在工作区内）
- shutil 模块：shutil.copy / shutil.move
- 变量在对话期间持续存在
- 最终给用户的文件写到 OUTPUT_DIR，平台自动检测上传
- 中间计算结果写到 STAGING_DIR

典型用法——一步到位发现文件并分析：
  import os
  files = os.listdir('.')
  print("工作区文件:", files)
  for f in files:
      if f.endswith(('.xlsx', '.csv')):
          df = pd.read_excel(f) if f.endswith('.xlsx') else pd.read_csv(f)
          print(f"\n{f}: {df.shape[0]}行×{df.shape[1]}列, 列: {df.columns.tolist()}")

注意事项：
- os 只能访问工作区内的文件，越界报 PermissionError
- 删除操作需先用 ask_user 确认，确认后在 confirm_delete 参数传入文件名
- read_excel/read_csv 默认截断 2000 行。截断时会提示，大数据请用 data_query 或 nrows=None
- 中文 CSV 读取报 UnicodeDecodeError 时，尝试 encoding='gbk' 或 encoding='gb18030'
- 禁止 import sys/subprocess
- 环境可能因超时被重置，如果变量不存在请重新读取文件
```

### 11.4 连锁修改更新

场景验证发现的 3 个问题新增 2 项修改（问题 B/C 仅影响提示词，已在 §11.3 覆盖）：

| # | 改动 | 文件 | Phase | 说明 |
|---|------|------|-------|------|
| 21 | PandasProxy 截断提示 | `sandbox_worker.py` | 1 | ~8 行改（_wrap_pd_reader） |
| 22 | kernel_worker PandasProxy 同步 | `kernel_worker.py` | 2 | 同上逻辑 |

---

## 12. 设计自检

- [x] 从运行链路出发（§1），不是从能力补偿出发
- [x] 明确了每轮工具调用在链路中解决什么问题（§1.1）
- [x] 明确了 os 开放后链路天然怎么走（§1.2）
- [x] 明确了 os 不能替代的 3 个场景及正确工具（§1.3）
- [x] 每个"隐藏能力"的归属分析基于链路而非补丁（§1.4）
- [x] os 的能力范围和边界有完整定义（§2）
- [x] scoped_os 与现有 7 层安全的协作关系明确（§2.4）
- [x] 工具精简决策基于"链路中是否有不可替代的价值"（§3.1）
- [x] FilePathCache 处理基于"os 链路下注册来源如何变化"（§3.2）
- [x] 提示词改造包含完整代码示例和路由规则（§6 + §11.3）
- [x] confirm_delete 全链路传递到位（§5）
- [x] 攻击面测试矩阵 15 个用例（§7.2）
- [x] 15 个用户场景全链路模拟验证（§11.1）
- [x] 3 个发现问题均有明确修复方案（§11.2）
- [x] 提示词最终版整合所有修复（§11.3）

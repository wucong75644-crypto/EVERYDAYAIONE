# 有状态代码执行（Stateful Code Execution）

> 版本：v2.0（2026-05-02）
> 状态：技术设计完成，待实施
> 任务等级：A级（涉及 5+ 文件 + sandbox 核心架构 + 新外部依赖 nsjail）

## 背景

当前 code_execute 每次启动全新子进程，变量不保留。用户做财务分析时模型把计算拆成多次调用，每次都要重新读文件，浪费严重（实测 9 次 code_execute 消耗 170K tokens）。

行业标准（ChatGPT Code Interpreter）使用有状态 Kernel，变量跨调用保留。

## 当前架构

```
用户消息 → LLM 生成 code_execute 调用
  → ToolExecutor._code_execute()
  → build_sandbox_executor() 创建 SandboxExecutor
  → SandboxExecutor.execute()
    → 1. validate_code() AST 预检
    → 2. _snapshot_output_files() 文件快照
    → 3. multiprocessing.Process(spawn) 全新子进程
    → 4. sandbox_worker_entry() 执行代码
    → 5. 进程退出，变量销毁
    → 6. _auto_upload_new_files() 检测上传新文件
```

核心文件：
- `backend/services/sandbox/executor.py`（SandboxExecutor，365 行）
- `backend/services/sandbox/sandbox_worker.py`（子进程入口，414 行）
- `backend/services/sandbox/sandbox_constants.py`（白名单模块，115 行）
- `backend/services/sandbox/validators.py`（AST 验证，125 行）
- `backend/services/sandbox/functions.py`（build_sandbox_executor 工厂，73 行）
- `backend/services/agent/tool_executor.py`（_code_execute 调用入口，第 428-499 行）

当前安全模型（7 层防护）：
| 层级 | 措施 | 位置 |
|------|------|------|
| L1 | AST 预检（18 个黑名单模块 + 危险函数） | validators.py |
| L2 | 运行时 import 白名单（43 个允许模块） | sandbox_constants.py |
| L3 | builtins 白名单（25 个安全函数） | sandbox_constants.py |
| L4 | 文件路径白名单（workspace/staging/output） | sandbox_worker.py:325-391 |
| L5 | 环境变量清理（15 组敏感前缀） | sandbox_worker.py:108-113 |
| L6 | 内存限制 RLIMIT_AS 2GB | sandbox_worker.py:120-125 |
| L7 | fork 防护 RLIMIT_NPROC=0 | sandbox_worker.py:128-131 |

## 目标架构

```
用户消息 → LLM 生成 code_execute 调用
  → ToolExecutor._code_execute()
  → SandboxExecutor.execute()
    → 1. validate_code() AST 预检（不变）
    → 2. _snapshot_output_files() 文件快照（不变）
    → 3. KernelManager.get_or_create(conversation_id)
       → 复用已有 nsjail 进程（变量还在）
       → 或降级为无状态 subprocess（Kernel 满时）
    → 4. 通过 stdin/stdout JSON-Line 发送代码、接收结果
    → 5. _auto_upload_new_files() 检测上传新文件（不变）
```

## 评审结论（2026-05-02）

多角色评审共识：
1. 方向正确，有状态执行是行业标准，用户价值明确
2. 安全隔离选 **nsjail**（namespace + cgroups v1 + seccomp + chroot 四合一）
3. IPC 协议选 **stdin/stdout JSON-Line**（最简单、nsjail 零配置透传）
4. 最多 4 个并发 Kernel（4核8GB 服务器），超出降级无状态
5. 保留现有 L1-L7 安全层作为内层防线，nsjail 作为外层防线

## 核心设计

### 1. IPC 协议（JSON-Line）

主进程通过 stdin 向 kernel_worker 发送请求，kernel_worker 通过 stdout 返回结果。每行一个 JSON。

**请求格式（主进程 → kernel_worker stdin）：**
```json
{"id": "req_001", "code": "import pandas as pd\ndf = pd.read_excel('data.xlsx')\nprint(df.shape)", "timeout": 120}
```

**成功响应（kernel_worker stdout → 主进程）：**
```json
{"id": "req_001", "status": "ok", "result": "(1000, 15)", "elapsed_ms": 230}
```

**错误响应：**
```json
{"id": "req_001", "status": "error", "result": "FileNotFoundError: 'data.xlsx'", "elapsed_ms": 5}
```

**超时响应：**
```json
{"id": "req_001", "status": "timeout", "result": "⏱ 执行超时（120秒）", "elapsed_ms": 120000}
```

### 2. kernel_worker.py（长驻 REPL 进程）

新建文件 `backend/services/sandbox/kernel_worker.py`，预估 ~150 行。

复用现有 sandbox_worker.py 的安全初始化逻辑，改为 REPL 循环：

```
启动：
  _clean_env()                    # 清理敏感环境变量（复用）
  _apply_resource_limits()        # RLIMIT_AS 2GB（复用）
  globals = _build_sandbox_globals()  # 构建沙盒命名空间（复用）
  替换 builtins.open 为 _global_scoped_open（复用）

主循环：
  while True:
      line = sys.stdin.readline()
      if not line: break           # stdin 关闭 = 主进程要求退出

      request = json.loads(line)

      # 每次执行前重置安全关键项（防跨调用篡改）
      globals["__builtins__"] = SAFE_BUILTINS.copy()
      builtins.open = _global_scoped_open
      builtins.__import__ = restricted_import

      # 执行代码（复用 _exec_code，支持 sys.settrace timeout）
      result = _exec_code(request["code"], globals, request["timeout"])

      # 路径隐藏 + 截断（复用）
      result = hide_paths(result)
      result = truncate_result(result, max_result_chars)

      # 返回结果
      response = {"id": request["id"], "status": "ok", "result": result}
      sys.stdout.write(json.dumps(response) + "\n")
      sys.stdout.flush()

      # globals 中用户定义的变量（df, fig 等）保留到下次调用
```

关键设计点：
- `sandbox_globals` 在进程生命周期内持续存在 → 变量跨调用保留
- `__builtins__` 和 `builtins.open`/`__import__` 每次执行前重置 → 防止用户在第一次执行时覆盖安全函数，第二次执行时绕过检查
- `_exec_code` 的 `sys.settrace` timeout 机制不变 → 超时只中断当前执行，不杀进程
- matplotlib `plt.close("all")` 每次执行后调用 → 防止 figure 泄漏
- 字体缓存在进程启动时预热一次 → 后续执行不再重复加载

### 3. KernelManager（进程池管理）

新建文件 `backend/services/sandbox/kernel_manager.py`，预估 ~200 行。

```python
class KernelManager:
    MAX_KERNELS = 4              # 最大同时存活 Kernel 数（4核8GB 服务器）
    IDLE_TIMEOUT = 1200          # 空闲 20 分钟回收
    MAX_LIFETIME = 1800          # 最大存活 30 分钟（强制重建，防内存泄漏）

    async def get_or_create(
        self,
        conversation_id: str,
        workspace_dir: str,       # 宿主机路径
        staging_dir: str,         # 宿主机路径
        output_dir: str,          # 宿主机路径
    ) -> Optional[Kernel]:
        """获取或创建 Kernel，返回 None 表示降级为无状态"""

    async def execute(
        self,
        conversation_id: str,
        code: str,
        timeout: float,
    ) -> tuple[str, str]:         # (status, result)
        """向 Kernel 发送代码并等待结果"""

    async def shutdown(self, conversation_id: str) -> None:
        """销毁指定 Kernel"""

    async def cleanup_idle(self) -> None:
        """定时任务调用：清理空闲和超龄 Kernel"""

    def active_count(self) -> int:
        """监控用：当前活跃 Kernel 数"""
```

**Kernel 数据结构：**
```python
@dataclass
class Kernel:
    conversation_id: str
    process: asyncio.subprocess.Process  # nsjail 包裹的 kernel_worker
    lock: asyncio.Lock                   # 串行执行锁（防并发竞态）
    created_at: float                    # 创建时间
    last_active: float                   # 最后活跃时间
    host_workspace: str                  # 宿主机 workspace 路径（文件上传用）
    host_staging: str                    # 宿主机 staging 路径
    host_output: str                     # 宿主机 output 路径
```

**降级逻辑：**
```
get_or_create(conv_id):
    1. if conv_id in _kernels and process alive:
         return existing kernel（更新 last_active）
    2. if len(_kernels) < MAX_KERNELS:
         spawn new nsjail + kernel_worker
         return new kernel
    3. else:
         尝试驱逐最久空闲的 Kernel
         if 驱逐成功:
             spawn new kernel
             return new kernel
         else:
             return None → executor fallback 到无状态 subprocess
```

**定时清理（每 60 秒）：**
```
cleanup_idle():
    for kernel in _kernels:
        if now - kernel.last_active > IDLE_TIMEOUT:
            shutdown(kernel)  # 空闲超时
        elif now - kernel.created_at > MAX_LIFETIME:
            shutdown(kernel)  # 超龄强制重建
```

**崩溃检测与恢复：**
- 每次 execute() 前检查 `process.returncode is not None` → 进程已死亡
- 自动移除死亡 Kernel，下次 get_or_create 时重建
- 返回"环境已重置，变量已清空，请重新执行"

**生命周期管理：**
- 在 `main.py` lifespan 中初始化 KernelManager（与 BackgroundTaskWorker 同模式）
- 启动时创建 cleanup_idle 定时任务
- shutdown 时遍历销毁所有 Kernel

### 4. SandboxExecutor 改造

修改 `executor.py`，execute() 新增有状态分支：

```
execute(code, description):
    1. validate_code(code)                    # AST 预检（不变）
    2. _snapshot_output_files()               # 文件快照（不变）
    3. _backup_existing_files()               # 文件备份（不变）

    4. # 新增：尝试有状态执行
       if self._kernel_manager:
           kernel = await self._kernel_manager.get_or_create(
               self._conversation_id,
               self._workspace_dir, self._staging_dir, self._output_dir
           )
           if kernel:
               status, result = await self._kernel_manager.execute(
                   self._conversation_id, code, self._timeout
               )
           else:
               # 降级：Kernel 满，走无状态 subprocess
               result = await self._run_in_subprocess(code)
       else:
           # 降级：KernelManager 不可用，走无状态 subprocess
           result = await self._run_in_subprocess(code)

    5. _dedup_overwritten_files()             # 文件去重（不变）
    6. _auto_upload_new_files()               # 自动上传（不变）
    7. return result
```

关键：步骤 1/2/3/5/6/7 完全不变，对外接口 `execute(code, description) → str` 不变。

### 5. nsjail 安全隔离

**选型理由：**

| 方案 | 淘汰原因 |
|------|---------|
| Firecracker 微虚拟机 | 需要 KVM + 复杂运维，杀鸡用牛刀 |
| gVisor | 需要容器化环境，项目无 Docker |
| Docker 容器 | 启动慢（秒级），需引入 Docker 依赖 |
| seccomp 单独用 | numpy 依赖 mmap/futex，白名单维护噩梦 |
| 仅 cgroups | 只管资源限制，不防文件逃逸/网络 |
| **nsjail** | **四合一，单文件部署，Google 出品** |

**nsjail 配置（deploy/sandbox.cfg）：**

```protobuf
name: "sandbox"
mode: ONCE
hostname: "sandbox"
time_limit: 0                    # 不限（由应用层控制 MAX_LIFETIME）

# --- 文件系统隔离 ---
mount { src: "/usr"                dst: "/usr"        is_bind: true  rw: false }
mount { src: "/lib"                dst: "/lib"        is_bind: true  rw: false }
mount { src: "/lib64"              dst: "/lib64"      is_bind: true  rw: false }
mount { src: "/etc/alternatives"   dst: "/etc/alternatives" is_bind: true rw: false }
mount { src: "/etc/mime.types"     dst: "/etc/mime.types"   is_bind: true rw: false }
mount { src: "/usr/share/zoneinfo" dst: "/usr/share/zoneinfo" is_bind: true rw: false }
# Python venv 只读
mount { src: "/var/www/everydayai/backend/venv" dst: "/venv" is_bind: true rw: false }
# workspace/staging/output 可写（运行时通过命令行参数动态指定）
# -B {host_workspace}:/workspace
# -B {host_staging}:/staging
# -B {host_output}:/output
mount { dst: "/tmp" fstype: "tmpfs" rw: true }
mount { dst: "/proc" fstype: "proc" rw: false }

# --- 资源限制 (cgroups v1) ---
cgroup_mem_max: 1073741824           # 1GB per Kernel
cgroup_pids_max: 32                  # 最多 32 线程（numpy 需要）
cgroup_cpu_ms_per_sec: 800           # 80% 单核

# --- 网络隔离 ---
clone_newnet: true                   # 完全断网

# --- UID 映射 ---
uidmap { inside_id: "1000" outside_id: "1000" count: 1 }
gidmap { inside_id: "1000" outside_id: "1000" count: 1 }

# --- 资源硬限制 ---
rlimit_as: 2048                      # 2GB 虚拟内存
rlimit_fsize: 512                    # 单文件最大 512MB
rlimit_nproc: 0                      # 禁止 fork
```

**启动命令（KernelManager 内部）：**
```bash
nsjail --config /var/www/everydayai/deploy/sandbox.cfg \
    -B {host_workspace}:/workspace \
    -B {host_staging}:/staging \
    -B {host_output}:/output \
    -- /venv/bin/python3 -u /venv/lib/.../kernel_worker.py \
       /workspace /staging /output
```

注意：kernel_worker 接收的路径参数是 **jail 内路径**（`/workspace`、`/staging`、`/output`），不是宿主机路径。`_global_scoped_open` 的白名单用这些 jail 内路径构建，与 `os.path.realpath()` 结果一致。

**路径映射（双套路径）：**

| 用途 | 路径 | 谁使用 |
|------|------|--------|
| 宿主机路径 | `/mnt/oss-workspace/workspace/org/{org_id}/{user_id}/` | 主进程（文件上传、快照对比） |
| jail 内路径 | `/workspace`、`/staging`、`/output` | kernel_worker（代码执行、文件读写） |

KernelManager 的 Kernel 数据结构同时记录两套路径：`host_workspace`（给 executor 文件上传用）和 jail 内路径（给 kernel_worker 用）。

**nsjail fallback：**
如果 nsjail 二进制不存在或启动失败，KernelManager 降级为直接启动 `python3 kernel_worker.py`（无 jail），日志告警。现有 L1-L7 安全层仍然生效。

### 6. 孤儿进程清理

**问题**：FastAPI 主进程崩溃时，nsjail 子进程可能变成孤儿。

**解决方案：PR_SET_PDEATHSIG 双重保险**

```python
import ctypes, signal

def _set_pdeathsig():
    """父进程死亡时自动 SIGTERM 子进程（Linux only）"""
    PR_SET_PDEATHSIG = 1
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)

# KernelManager 启动 nsjail 时
process = await asyncio.create_subprocess_exec(
    "nsjail", "--config", cfg_path, "--", "python3", "-u", "kernel_worker.py", ...,
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    preexec_fn=_set_pdeathsig,
)
```

信号链：主进程死 → nsjail 收 SIGTERM → nsjail 内 kernel_worker 也被杀。

**KernelManager 启动时清理残留：**
- 检查 `/sys/fs/cgroup/memory/NSJAIL/` 下是否有残留 cgroup
- 清理残留（nsjail 正常退出会自动清理，异常退出可能残留）

### 7. 安全模型（纵深防御）

有状态模式下的完整安全分层：

| 层级 | 措施 | 防护目标 | 来源 |
|------|------|---------|------|
| **外层：nsjail** | namespace + cgroups + chroot | 文件系统逃逸、网络访问、资源耗尽 | 新增 |
| L1 | AST 预检 | 快速拦截明显恶意代码 | 保留不变 |
| L2 | import 白名单 | 阻止危险模块导入 | 保留不变 |
| L3 | builtins 白名单 | 禁止 eval/exec/getattr | 保留不变 + **每次执行前重置** |
| L4 | 文件路径白名单 | 限制文件访问范围 | 保留不变（用 jail 内路径） |
| L5 | 环境变量清理 | 防止凭证泄露 | 保留不变 |
| L6 | RLIMIT_AS 2GB | 内存硬限制 | 保留不变 |
| L7 | RLIMIT_NPROC=0 | 禁止 fork | 保留不变（在 nsjail 内设置） |

**有状态特有的安全措施：**
- `__builtins__` 每次执行前重置 → 防止用户跨调用篡改安全函数
- `builtins.open` 每次执行前重置 → 防止用户覆盖文件访问控制
- `builtins.__import__` 每次执行前重置 → 防止用户绕过 import 白名单
- cgroups v1 memory 限制 1GB → 触及上限 OOM killer 杀进程，KernelManager 自动重建
- MAX_LIFETIME 30 分钟 → 防止长期内存泄漏积累

## 边界场景处理

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| Kernel OOM/段错误 | 检测 process.returncode，自动重建，返回"环境已重置" | KernelManager |
| Kernel 空闲超时（20分钟） | cleanup_idle 定时扫描，超时销毁释放资源 | KernelManager |
| 达到最大 Kernel 数（4个） | 驱逐最久空闲的；仍满则降级无状态 subprocess | KernelManager + SandboxExecutor |
| nsjail 启动失败 | fallback 为裸 subprocess + 日志告警 | KernelManager |
| 主进程崩溃/重启 | PR_SET_PDEATHSIG 自动杀子进程；主进程重启后从空状态开始 | KernelManager + preexec_fn |
| 并发请求同一 conversation | Kernel 内 asyncio.Lock 排队执行 | Kernel.lock |
| 用户代码死循环 | sys.settrace timeout 中断当前执行，不杀 Kernel 进程 | kernel_worker |
| 用户代码污染 builtins | 每次执行前重置 builtins/__import__/open | kernel_worker |
| Kernel 内存持续增长 | cgroups v1 memory 1GB 限制 + MAX_LIFETIME 30 分钟强制重建 | nsjail + KernelManager |
| 服务器重启 | systemd 拉起主进程，Kernel 按需创建 | systemd |

## 文件结构

### 新增文件
- `backend/services/sandbox/kernel_manager.py`：Kernel 进程池管理（~200 行）
- `backend/services/sandbox/kernel_worker.py`：长驻 REPL 进程（~150 行）
- `deploy/sandbox.cfg`：nsjail 配置文件
- `deploy/install_nsjail.sh`：nsjail 编译安装脚本

### 修改文件
- `backend/services/sandbox/executor.py`：execute() 新增有状态分支
- `backend/services/sandbox/functions.py`：build_sandbox_executor 注入 KernelManager
- `backend/services/agent/tool_executor.py`：timeout 语义微调（从进程生命周期 → 单次执行）
- `backend/main.py`：lifespan 中初始化/清理 KernelManager
- `deploy/everydayai-backend.service`：启用 systemd 安全加固

### 不变的文件
- `backend/services/sandbox/validators.py`：AST 预检逻辑不变
- `backend/services/sandbox/sandbox_constants.py`：白名单/黑名单不变
- 文件上传机制：snapshot diff + auto_upload 在主进程执行，不受 nsjail 影响
- 图片尺寸提取：主进程用宿主机路径读取，不受影响

## 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增 KernelManager | kernel_manager.py（新建） | 进程池 + 超时回收 + 降级 |
| 新增 kernel_worker | kernel_worker.py（新建） | REPL 循环 + builtins 重置 |
| 新增 nsjail 配置 | deploy/sandbox.cfg（新建） | cgroups v1 + namespace |
| SandboxExecutor.execute() | executor.py | 有状态分支 + fallback |
| build_sandbox_executor | functions.py | 注入 KernelManager |
| _code_execute timeout | tool_executor.py:454-457 | 语义从进程生命周期 → 单次执行 |
| RLIMIT_NPROC 设置位置 | sandbox_worker.py:128-131 | 改在 nsjail 内设置 |
| main.py lifespan | main.py | 初始化/清理 KernelManager |
| 提示词切换 | agent 提示词 | 无状态 → 有状态 |
| systemd 加固 | everydayai-backend.service | 启用安全选项 |
| nsjail 安装 | deploy/install_nsjail.sh（新建） | 编译安装 + 依赖 |

## 服务器环境

| 项目 | 值 |
|------|-----|
| 系统 | Alibaba Cloud Linux 3（RHEL 8 系） |
| 内核 | 5.10.134（满足 nsjail 要求的 4.6+） |
| CPU | 4 核 |
| 内存 | 7.3GB |
| Python | 3.11.13 |
| cgroups | v1（memory/pids/cpu 控制器均可用） |
| user namespace | 已启用（max=29760） |
| 包管理 | dnf |

**需要安装的依赖（写入 install_nsjail.sh）：**
```bash
dnf install -y epel-release
dnf install -y gcc-c++ protobuf-compiler libnl3-devel libcap-devel make
# 编译 nsjail
git clone https://github.com/google/nsjail.git /opt/nsjail-src
cd /opt/nsjail-src && make && cp nsjail /usr/local/bin/
```

**systemd 安全加固（deploy/everydayai-backend.service）：**
```ini
User=everydayai                  # 不再用 root
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
```

## 提示词

### 有状态版本（改造完成后切换）

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

### 当前无状态版本（过渡期，commit f7a16fb 已 revert 为此版本）

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

## 开发任务拆分

### Phase 1：kernel_worker 长驻 REPL（纯 Python，无 nsjail）
- [ ] 1.1 新建 kernel_worker.py：stdin/stdout JSON-Line 循环 + 复用 _build_sandbox_globals / _exec_code / _clean_env
- [ ] 1.2 每次执行前重置 builtins/__import__/open
- [ ] 1.3 单元测试：通过 subprocess 启动 kernel_worker，发送多次代码，验证变量跨调用保留

### Phase 2：KernelManager 进程池
- [ ] 2.1 新建 kernel_manager.py：get_or_create / execute / shutdown / cleanup_idle
- [ ] 2.2 asyncio 定时任务：每 60 秒扫描 idle kernel，超时回收
- [ ] 2.3 降级逻辑：超出 MAX_KERNELS 时 fallback 无状态
- [ ] 2.4 崩溃检测：process.returncode 非 None 时自动重建
- [ ] 2.5 PR_SET_PDEATHSIG：preexec_fn 设置父死子死
- [ ] 2.6 单元测试：并发请求、超时回收、崩溃恢复、降级

### Phase 3：SandboxExecutor 集成
- [ ] 3.1 executor.execute() 新增有状态分支
- [ ] 3.2 functions.py 注入 KernelManager（单例，lifespan 管理）
- [ ] 3.3 main.py lifespan 初始化/清理 KernelManager
- [ ] 3.4 tool_executor.py timeout 语义调整
- [ ] 3.5 集成测试：端到端 code_execute → kernel → 结果 → 文件上传

### Phase 4：nsjail 隔离
- [ ] 4.1 编写 install_nsjail.sh：在 Alibaba Cloud Linux 3 上编译安装
- [ ] 4.2 编写 sandbox.cfg：cgroups v1 + namespace + 文件系统挂载
- [ ] 4.3 KernelManager 启动命令改为 nsjail 包裹
- [ ] 4.4 双套路径映射：宿主机路径（文件上传用）+ jail 内路径（kernel_worker 用）
- [ ] 4.5 nsjail fallback：二进制不存在时降级为裸 subprocess
- [ ] 4.6 服务器部署验证：安装 nsjail + pandas/matplotlib 测试

### Phase 5：systemd 加固 + 提示词切换
- [ ] 5.1 启用 systemd 安全选项（NoNewPrivileges、ProtectSystem 等）
- [ ] 5.2 提示词从无状态切换为有状态版本
- [ ] 5.3 全量回归测试

### Phase 6：监控 + 文档
- [ ] 6.1 日志：Kernel 创建/销毁/降级/崩溃恢复事件
- [ ] 6.2 更新 TECH_ARCHITECTURE.md
- [ ] 6.3 更新 PROJECT_OVERVIEW.md、FUNCTION_INDEX.md

## 依赖变更

**服务器安装（非 Python 依赖）：**
- nsjail：从源码编译
- 编译依赖：gcc-c++、protobuf-compiler、libnl3-devel、libcap-devel、make（通过 dnf + epel-release 安装）

**Python 依赖：无新增。** kernel_worker 复用现有 sandbox 代码。

## 部署与回滚策略

**部署步骤：**
1. 服务器执行 install_nsjail.sh 编译安装（一次性）
2. 部署新代码（deploy.sh 正常流程）
3. systemctl restart everydayai-backend

**回滚方案：**
- 代码级回滚：revert commit 即可
- nsjail 不可用时自动 fallback 为无状态 subprocess（内置降级）
- 无数据库迁移需要回滚
- nsjail 安装不影响其他服务（独立二进制文件）

## 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| nsjail 在 Alibaba Cloud Linux 3 编译失败 | 中 | Phase 4 先在服务器测试编译；失败则 fallback 为无 nsjail 的长驻进程 + 现有 L1-L7 |
| 长驻进程内存泄漏 | 中 | cgroups v1 memory 1GB + MAX_LIFETIME 30 分钟强制重建 |
| 并发 Kernel 超出内存 | 低 | MAX_KERNELS=4 × 1GB < 8GB，且有降级策略 |
| kernel_worker 执行阻塞 stdin | 低 | sys.settrace timeout 保证单次执行有上限 |
| 用户跨调用篡改安全函数 | 低 | builtins/__import__/open 每次执行前重置 |

## 参考

- ChatGPT Code Interpreter：Firecracker 微虚拟机 + 20 分钟超时
- Replit：gVisor 用户态内核 + 容器
- Google Colab：GCE 虚拟机 + 容器
- JupyterHub：Docker/K8s 容器
- nsjail：https://github.com/google/nsjail（Google 出品，轻量级沙盒）
- 当前项目 sandbox：`backend/services/sandbox/`

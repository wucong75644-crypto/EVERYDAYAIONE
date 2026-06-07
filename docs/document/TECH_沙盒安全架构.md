# 沙盒安全架构 (Phase 1 单层 OS 隔离)

> 状态:已实施 (2026-06-08)
> 适用范围:`backend/services/sandbox/`
> 替代:旧"Python 层路径白名单 + nsjail 双层防御"

---

## 核心原则

**安全边界 = nsjail (生产) / 信任 (本地开发)。Python 层不做安全校验,只做 UX 引导。**

对齐行业标准:
- OpenAI Code Interpreter — gVisor + K8s,无 Python 层校验
- Anthropic Code Execution Tool / sandbox-runtime — bubblewrap (Linux) / Seatbelt (macOS),TypeScript 实现,零 Python 层
- E2B — Firecracker microVM,rootfs 默认全可读
- Modal — gVisor + Docker image
- 学术共识:Python introspection 逃逸 (`().__class__.__bases__[0].__subclasses__()`) 不可堵 → 语言层沙盒不可能

---

## 分层职责

```
┌───────────────────────────────────────────────────────────┐
│  应用层: LLM Agent (业务逻辑)                              │
├───────────────────────────────────────────────────────────┤
│  Python 层: UX 引导 (不做安全)                             │
│  • SAFE_BUILTINS         禁 eval/exec/input/open (早 fail)│
│  • BLOCKED_IMPORT_MODULES 禁 subprocess/ctypes/socket 等  │
│                          (UX,挡善意代码,挡不住攻击者)    │
│  • validators.py AST     禁 eval/exec/compile 调用形式     │
│  • scoped_os.remove/rmdir 引导用 file_delete 工具          │
│  • scoped_open / scoped_os 路径解析 (相对→绝对) + 文件名纠错│
│  • emit_auto_hooks       IPython/plotly/altair 自动 emit  │
├───────────────────────────────────────────────────────────┤
│  ★ 安全边界: nsjail (deploy/sandbox.cfg)                  │
│  • bind mount: /workspace /staging /output 可写            │
│                /usr /lib /lib64 /venv /app 只读 (ro)        │
│  • clone_newnet: true   网络命名空间隔离 (完全断网)         │
│  • cgroup_mem_max: 4GB  内存上限                          │
│  • cgroup_pids_max: 128 进程数上限 (防 fork bomb)          │
│  • cgroup_cpu_ms_per_sec: 800ms/sec  CPU 配额             │
│  • rlimit_fsize: 512MB / rlimit_nofile: 256 / 进程硬限制    │
├───────────────────────────────────────────────────────────┤
│  Linux 内核: namespace + cgroup + seccomp                  │
└───────────────────────────────────────────────────────────┘
```

---

## 为什么删 Python 层路径白名单

### 历史踩雷(全是反模式后果)

| 时间 | 现象 | 旧方案 |
|------|------|--------|
| matplotlib 字体被拒 | `/venv/.../DejaVuSans.ttf 不在允许的目录内` | 加 venv 到只读白名单 |
| plotly 模板被拒 | 同上 | 加 venv 子路径 |
| `/etc/mime.types` 被拒 | mimetypes 库读 mime 数据库 | 加 `_readonly_system_files` |
| `/usr/share/zoneinfo` 被拒 | datetime 读时区 | 同上 |
| scipy `.so` 加载偶发被拒 | scipy 内部资源 | 同上 |

每个新装第三方库都可能踩雷 → 白名单永远补不完。

### 行业事实

调研主流方案 (OpenAI/Anthropic/E2B/Modal/Replit/Daytona),**0 家在 Python 层做白名单**。
理由:
1. Python introspection 5 行代码绕过任何 builtins/import/open hook → 挡不住攻击者
2. 双层校验 = 双倍 bug 面积 + 错误信号噪声 (PermissionError 99% 是无辜的库被拦)
3. 给团队"虚假安全感"

### 我们生产 nsjail 已经够

`deploy/sandbox.cfg` (摘要):
```cfg
mount { src: "/usr"   rw: false }    # /usr 只读
mount { src: "/lib"   rw: false }    # /lib 只读
mount { src: "/venv"  rw: false }    # Python 库只读
mount { src: "/app"   rw: false }    # 业务代码只读
clone_newnet: true                    # 网络完全切断
cgroup_mem_max: 4294967296            # 4GB 内存上限
```

LLM 写 `open("/etc/passwd", "w")` → OS 层 EROFS。
LLM 写 `socket.socket(...)` → OS 层 ENETUNREACH。
LLM 写 fork bomb → cgroup pids_max 杀。
Python 层校验是冗余 (rubber stamp)。

---

## 保留的 Python 层 UX 引导(明确不是安全)

| 项 | 性质 | 删了会怎样 |
|----|------|----------|
| `BLOCKED_IMPORT_MODULES` 含 socket/urllib/requests 等 | LLM UX 早 fail | 写 `requests.get(...)` → 运行时 "Network unreachable",信号差 |
| `BLOCKED_IMPORT_MODULES` 含 subprocess/ctypes | LLM UX 早 fail | 同上 |
| `SAFE_BUILTINS` 禁 eval/exec/open/input | LLM UX | open 会绕过 scoped_open 路径解析 + 纠错 |
| `validators.py` AST 禁 eval/exec/compile | 静态分析早 fail | 没影响功能,只是错误时机更晚 |
| `scoped_os.remove/rmdir/rmtree` 引导用 file_delete | UX | LLM 删错文件没回滚机制 |
| `scoped_open` 路径解析 + 文件名纠错 | 业务功能 | LLM 写错文件名直接 IOError |

---

## 本地开发(无 nsjail)

macOS / 没 nsjail 的 Linux:
- 沙盒 fallback 到"裸 python + symlink"模式 (kernel_manager.py 自动判断)
- 安全模型: **开发机被信任**(开发者写自己的代码,本就能 rm -rf 自己)
- 应急开关: 暂未实现 `SANDBOX_STRICT=1` 退回旧白名单 (Phase 2 视需要)

---

## 部署 checklist

生产部署前**必须**校验 `deploy/sandbox.cfg`:

- [ ] `clone_newnet: true` (防出网)
- [ ] `/usr`, `/lib`, `/lib64`, `/venv`, `/app` 均 `rw: false`
- [ ] `cgroup_mem_max`, `cgroup_pids_max`, `cgroup_cpu_ms_per_sec` 全部设置
- [ ] `rlimit_fsize`, `rlimit_nofile`, `rlimit_as` 全部设置
- [ ] 没有 `mount_proc: true` (procfs 暴露主机信息)

POC 静态校验脚本:`backend/scripts/poc_sandbox_phase1.py` (Section A)

---

## 故意不动的(独立模块,与本架构无关)

| 模块 | 职责 | 为什么留 |
|------|------|--------|
| `services/file_executor.py::resolve_safe_path` | file_read/file_write/file_search 工具的 path traversal 防护 | **工具层** 防 LLM 用 `../../etc/passwd`,行业标准实践,与沙盒无关 |
| `api/routes/file.py` 上传扩展名白名单 | 业务需求 (防上传 `.exe`) | 同上 |
| `core/workspace.py::resolve_*_dir` | 按 org_id/user_id/conversation_id 分桶 | 多租户隔离,不接受用户输入 |

---

## 历史对比

| 维度 | Phase 1 前 | Phase 1 后 |
|------|----------|----------|
| 独立安全检查点 | 14 (沙盒侧) | 6 (全部 UX) |
| 路径白名单 | 2 套不一致 (`_scoped_open` + `_check_path`) | 0 |
| 库内资源被拒 (matplotlib 字体等) | 反复踩雷 | 永不触发 |
| 安全边界 | "Python 层 + nsjail" (虚假感) | "nsjail" (真实) |
| 维护负担 | 每装新库可能补白名单 | 0 |
| 与行业对齐 | 反模式 (无先例) | 对齐 Anthropic/OpenAI/E2B |

---

## 参考

- 调研报告: 见提交记录 `b6b9c47` (尚未提交) 的 Agent 报告
- POC 脚本: `backend/scripts/poc_sandbox_phase1.py` (27/27 通过)
- Anthropic 工程博客: <https://www.anthropic.com/engineering/claude-code-sandboxing>
- E2B 架构: <https://github.com/e2b-dev/E2B>
- HackTricks Python sandbox bypass: <https://book.hacktricks.xyz/generic-methodologies-and-resources/python/bypass-python-sandboxes>

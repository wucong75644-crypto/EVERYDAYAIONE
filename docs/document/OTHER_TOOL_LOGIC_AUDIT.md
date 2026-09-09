# 工具逻辑追加核验（2026-09-09）

> 最新状态：用户随后授权在核清调用链后修复。O-02/O-03 及同类 staging 登记入口已本地修复，41 个边界/兼容用例通过；最终主组 1481 + 独立 ERP 11 = 1492 passed，4 原有 xfailed。详见 [修复与兼容验收](FILE_TOOL_BOUNDARY_REPAIR.md)。未提交部署。下文保留修复前核验快照，其中“未修复”“与部署源码相同”描述的是当时状态。

用户询问其他工具是否被破坏，按当前代码与已部署候选 `e243ba2c0d545d8afca3ae930a40389276b7c123` 对照核验。范围是共享文件缓存全部生产调用方，以及媒体、ERP、定时任务、工具循环和确认流程的相关既有测试；不是全产品无缺陷证明。

## 发现与状态

| 编号 | 事实与影响 | 归属 | 状态 / 证据 |
|---|---|---|---|
| O-01 | 本次修复曾把路径归一化输入改成 basename。缓存存在 `甲/report.csv` 时，输入 `乙/report-.csv` 会错误命中甲目录；已部署版本返回 None、交给旧直接路径解析器 | 本次未部署修复引入的回归 | **已修复**：恢复以原输入归一化，增加 test_directory_qualified_path_does_not_gain_basename_fuzzy_fallback，包含实际分析路径解析对照。最终本组 1111 passed、4 原有 xfailed |
| O-02 | `_file_delete` 将已存在的绝对路径直接传给 os.remove，缓存/ID 命中后也未统一走 executor.resolve_safe_path。真实工作区守卫会拒绝的外部 fixture，在原删除 Handler 中模拟删除调用 1 次、工作区校验 0 次 | 已部署旧逻辑；相关行来自 `a04534ae9`（2026-06-14）；文件与部署候选逐字节相同 | **未修复**。只在 mock 下验证，文件仍存在；正常 Chat 的危险操作确认仍在，此缺口不代表绕过确认，而是批准之后缺少资源路径边界校验 |
| O-03 | `_register_files_from_output` 从沙盒 stdout 提取 `../outside-fixture.csv` 后，先按 basename 注册 realpath，再检查相对路径；检查未通过不能撤销前面的登记，导致工作区外路径进入共享缓存 | 已部署旧逻辑；sandbox_tool_mixin 与部署候选逐字节相同 | **未修复**。仅临时 fixture 复现缓存污染，不声称已验证生产读到敏感文件或发生真实误删 |

O-02/O-03 属于同一资源边界问题的入库与使用两端。建议发布前补齐：缓存登记前按可信工作区边界校验；删除时无论输入来自 ID、缓存、相对还是绝对路径，都在产生副作用前经过相同的工作区与禁止文件校验。不能以“用户确认删除”替代文件归属校验，也不能以缓存命中作为授权依据。当前用户本轮要求核查，因此未擅自改写这两个旧业务入口。

## 可复核证据

- [诊断脚本](tool-unification-evidence/audit-other-file-tools.py) 与 [结果](tool-unification-evidence/03-other-file-tools-audit.txt)：真实 FileExecutor 守卫 + 原 Handler/登记函数，删除与记录均 mock。有效 ID 模拟删除 1 次，未知/错组织 ID 为 0 次；工作区外绝对路径模拟删除 1 次且守卫为 0 次；外部 stdout 路径被登记；fixture 文件仍存在。源码与已部署版本一致断言通过。
- 文件/工具统一复验：[命令](tool-unification-evidence/run-file-cache-regression.sh)、[日志](tool-unification-evidence/03-file-cache-regression.txt)、[指纹](tool-unification-evidence/03-file-cache-checks.txt)。新增身份测试现为 22 项，原先 21 项修复前基准证据继续保留，第 22 项为本轮发现的回归保护。1111 passed、4 原有 xfailed，不将 O-02/O-03 的缺陷诊断计成安全测试通过。
- 补查 80 passed：[日志](tool-unification-evidence/03-other-tools-regression.txt)。准确命令：`python -m pytest backend/tests/test_sandbox_tool_mixin.py backend/tests/test_media_tool_executor.py backend/tests/test_tool_loop_tooloutput.py backend/tests/test_scheduled_task_agent.py backend/tests/test_scheduled_task_workflow.py backend/tests/test_tool_confirm.py backend/tests/test_ws_tool_confirmation.py backend/tests/test_tool_loop_helpers.py backend/tests/test_chat_tool_loop.py -o addopts='' -v --tb=short`。
- ERP 独立进程 11 passed：[日志](tool-unification-evidence/03-other-tools-erp.txt)。命令：`python -m pytest backend/tests/test_erp_tool_mixin_unit.py -o addopts='' -v --tb=short`。沿用已知测试隔离方式，不与会改写 sys.modules 的 ERP 文件混合收集。
- 环境同附件 ID 修复：当前任务工作树，Python 3.14.2，APP_ENV=testing、数据库和 Redis 测试端口 1、JWT 测试占位；不加载生产配置。全部 **1202 passed、4 原有 xfailed**；额外警告为既有 asyncio.iscoroutinefunction 弃用。

检查旧测试还发现，test_sandbox_tool_mixin 对输出登记仅检查“不抛异常”，注释甚至称其为空操作，与当前实现不符；这解释了为什么既有测试通过没有发现 O-03。新增 ID 正反向测试也不能替代路径权限测试。

本轮没有部署、真实删除、真实媒体生成或外部业务写入。O-01 已解决；O-02/O-03 未解决，不能将本轮检查标为“全部工具安全通过”，建议先修复这两处再发布候选。

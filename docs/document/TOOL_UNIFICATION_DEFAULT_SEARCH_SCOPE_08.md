# F08-05 普通聊天默认搜索范围修复

状态：**默认范围调整已实现，本地验证通过，待部署/用户验证。** 当前 HEAD `8428b72c0c8b36481e5fdbc3bcb3e93070c2f1f9` 加本任务未提交差异；不能称为该 SHA 已包含本次修复。被测源码见 [独立指纹](tool-unification-evidence/08-file-protocol/default-scope-tested-sources.json)。本记录不覆盖此前文件名改写的未通过结论，也不意味着整体验收通过。

## 已确认需求与实现依据

用户要求“默认去工作区和聊天内搜索”，随后确认包括当前对话历史附件。普通聊天输入名字时，不必要求用户每次补“工作区”。

`api/routes/file_upload.py` 的 upload/upload_to_workspace 已把上传保存到当前 owner 工作区，返回 workspace_path；企微附件服务沿用该路径。当前任务 ResourceManifest 仅冻结本轮附件，但历史上传原文件仍在同一工作区。因而按获准工作区现有查询即可覆盖本轮、历史附件和其他工作区文件，不需要再扫聊天正文、查其他对话消息或建立第二套索引。工作区内同一文件只返回一次；已删除的源文件不会因旧聊天引用而被当作存在。仅有外部 URL、没有 workspace_path 的历史载荷不在文件搜索能力内，本次不自动下载远程内容。

## 具体合同

| 场景 | 最终行为 |
|---|---|
| interactive 普通聊天省略 file_search.scope | 默认 workspace，在现有 owner、组织、资源动作授权过滤下查询，包括工作区内的聊天上传文件 |
| 明确 scope=current | 仅本轮附件；不把它扩为整个聊天历史，也不自动失败后扩大 |
| 明确 scope=workspace | 保留原工作区查询 |
| scheduled/preflight 省略 scope | 保留原清单/授权/浏览上下文规则，不改成普通聊天默认 |
| 同名存在于本轮、历史或其他工作区目录 | path 名字定位返回歧义；keyword 返回候选，不先选本轮而忽略其他同名文件 |
| file_analyze / delete / restore | 保留原身份解析、动作权限、确认及版本规则；搜索范围不是执行授权 |
| 历史载荷/缓存/回放 | 字段可读不变；新执行的 interactive 无 scope 搜索按新默认，已完成结果回放不重做；显式 current 与默认 workspace 缓存不混用 |

普通聊天的默认 workspace 不受先前一次 current 搜索污染；若用户要求持续只查本轮附件，模型应继续明确传 current。不存在新增 all/chat scope 字段、数据库迁移或跨组织/个人/群权限放宽。

## 修改位置与兼容

- `ResourceSelections.scope`：显式 scope 检查后，为 interactive file_search 提供 workspace 默认，早于原附件路径和浏览线索推导；所有生产入口共享此处。
- `FileToolMixin._file_search`：直接 Handler 入口复用同一默认判断；结构化错误使用实际 resolver.scope，不把默认工作区错误标成 current。
- `file_schemas.py`：scope 可省略，说明聊天上传位于工作区。撤回本任务前一候选“新模型 scope 必填、范围不明先问”的要求，服从本次用户明确的新行为决定。原 scope enum/类型和 legacy validation 无变化。
- 测试保留冻结的 07 基线，08 快照显式记录新说明；参数必填及类型兼容逐字段比较恢复全部通过。

本次是用户授权的默认行为调整，不能把它写成“所有旧无 scope 执行语义完全不变”。file_analyze 的省略范围推导未改，其同批快照隔离仍由真实延迟调度测试验证。

## 验证

| 验证 | 结果 | 证据 |
|---|---|---|
| 新增默认搜索集 | 23 passed：ask/auto/plan × legacy/chat/loop；本轮/历史/工作区去重；精确 current；重名；有限授权；组织/个人/群隔离；scheduled/preflight；确认批准/拒绝；模型循环缺省和缓存隔离；错误范围 | `backend/tests/test_file_search_default_scope_08.py`；[最终定向命令](tool-unification-evidence/08-file-protocol/protocol-test-command-000641.json) |
| 30 个相关模块 | 1777 passed、0 failed/skipped/xfailed | [命令](tool-unification-evidence/08-file-protocol/protocol-test-command-000523.json)、[日志](tool-unification-evidence/08-file-protocol/protocol-tests-000523.log) |
| 直接 Handler 错误范围补验及关联模块 | 200 passed，含新补的第 23 个默认场景，0 failed/skipped/xfailed；不与上组重复累加 | [命令](tool-unification-evidence/08-file-protocol/protocol-test-command-000641.json)、[日志](tool-unification-evidence/08-file-protocol/protocol-tests-000641.log) |
| 实际模型遗漏参数回归 | 已用此前空白会话返回的原名 keyword 无 scope 参数作为本地回归输入，真实 prepare/Handler 命中临时文件；没有新增 provider 调用 | `test_captured_fresh_model_arguments_find_file_without_scope`；原模型 [4 次记录](TOOL_UNIFICATION_FRESH_CONVERSATION_08.md) |
| 静态/隔离 | git diff --check 通过；主工作树保持无修改；无生产写入、推送、部署或关闭 | Git/本任务工具记录 |

测试仅连接配置中的不可达本地测试 DB/Redis，身份通过既有 IdentityDB fixture 提供；文件查询/路径守卫/策略/Dispatcher/Handler 是实际实现。删除只作用于可丢弃临时文件，生产文件和 OSS 均不变。pytest-env 未识别与 Pydantic 旧 Config 警告保留。

最初 4 项失败均已复验：确认拒绝测试改为检查 Runtime 的 not-started 结果；两项附件专用测试明确 scope=current；同批不得继承后完成浏览的时序测试保留在仍执行范围推导的 file_analyze 上，继续检查拒绝且 0 Handler。新默认检索在独立测试中验证，不通过降低原权限/确认断言制造通过。

## 剩余状态与用户验证

默认遗漏问题本地已修复；本次没有新增真实模型调用，也尚未部署新候选，所以不能断言模型以后永不误选 current。先前原会话名字改写仍有真实失败，F08-05 全部行为及工具统一整体验收仍未通过；F08-03/F08-04 也未在本次修改。

部署需用户再次指令“提交部署”。部署最终版本后最小验证：

1. 新对话输入原文件名并要求查找，不写“工作区”，应找到已上传原文件。
2. 当前对话有本轮附件和更早的历史附件时，分别按名字查找都能找到；同名多个候选时应要求选择。
3. 明确只查本轮附件，历史文件不得混入；用可丢弃文件验证删除拒绝保持文件、批准才删除。

技术、本次用户验收与受控关闭分开；不自动 accept-and-close。没有持久化迁移，若后续发布需回退，通过受控发布入口使用已推送版本，回退会恢复旧默认搜索限制。

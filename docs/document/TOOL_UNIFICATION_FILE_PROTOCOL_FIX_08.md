# F08-05 文件搜索协议修复：本地验证记录

## 最新增量：普通聊天默认搜索获准工作区和聊天上传文件（2026-09-14）

用户明确包含历史附件；上传原文件已在 owner 工作区，复用原查询统一检索。interactive file_search 缺省改为 workspace，显式 current/定时预检授权/删除确认保留，scope 不再要求模型必填。新增 23 项通过，相关 1777 项及最终定向 200 项通过（有重叠，不累加）。[合同、证据与限制](TOOL_UNIFICATION_DEFAULT_SEARCH_SCOPE_08.md)。本次未提交部署，默认范围本地验证通过；原会话名字改写及整体验收仍未关闭。下方为此前候选快照。


## 最新结论：新增 24 次真实模型验证已完成，候选未通过

用户明确增加额度后完成 12 次说明对照及 12 次候选验证。候选原会话 4 次仍有 3 次漏搜，完整路径改写和范围不明确场景另有 2 次失败；既有 ID/引用选择器按预期传递。候选共 7 满足边界预期 / 5 未通过，**不发布、不标已修复**。生产业务执行/写入均 0，模型额度已用完。详见 [真实模型逐项证据与下一修复任务](TOOL_UNIFICATION_FILE_PROTOCOL_MODEL_08.md)。以下“待调用授权/未验证”均为此前本地阶段快照。


状态：**代码候选已完成，本地相关验证通过；真实模型复验未验证，F08-05 技术验收仍未通过。未提交、未部署、未合并或受控关闭。**

基准/当前 HEAD 为 `8428b72c0c8b36481e5fdbc3bcb3e93070c2f1f9`，分支 `codex/task/20260913102153-tool-unification-08-fixes`。本次被测对象是该 HEAD 加未提交差异，不能把基准 SHA 冒充修复版本。最终源文件 SHA256 见 [被测源码](tool-unification-evidence/08-file-protocol/tested-sources.json)。

## 原因与范围

既有生产证据表明：用户原文名字没有空格，模型发出的 file_search.keyword 首次出现空格，底层字面关键词查询因此未命中；一次调用还缺失 scope，落入空的当前附件范围。原 Excel 存在，失败轮次未调用 file_delete，不能认定删除 Handler 故障。

已完成的原会话 8 次对照仅改变 file_search 整份定义：当前组 3/4 改名、旧组 0/4 改名，但旧组 4/4 漏 scope。该结果支持定义影响模型行为，不能证明哪一句或哪个单独提交造成问题；当前轮没有新增模型成功证据。历史文件 ID 已存在，缺口是发现阶段对完整文件名的引导及两层解析不一致。

主责 04 文件目标/调用合同，05 配合说明职责和错误展示。未重写搜索引擎、文件 ID、Policy、删除、审计、模型循环、数据库或 WS。

## 实际改动

- `file_schemas.py`：完整名字/路径使用 path，关键词使用 keyword；模型 schema 显式要求 scope。搜索说明移除下游分析长指引；附件、file_analyze 和搜索结果 read_call 保留原分析合同。file_delete schema 保持不变，避免无关确认指纹变化。
- `FileTargetResolver.resolve_search`：复用已有精确名字、归一化唯一性与严格路径机制。保留旧 current 附件无扩展名选择器（如 png）；没有新增 keyword 去空格、全局模糊搜索或自动扩大工作区。
- `file_calls.py` 与 `file_tool_mixin.py`：current/workspace 单文件使用同一定位入口；不吞掉重名/不完整/不存在错误再改走关键词。保留候选及范围信息，workspace 空搜索明确实际范围。
- 新模型 scope 必填，旧部分 validation/execute、旧选定工具 schema 和回放仍保留原缺省。新模型循环测试改为显式 scope；另以真实旧 `_execute_tools` 载荷验证缺省浏览连续性。未放宽校验器。
- 07 冻结基线不改，新增明确的 08 schema 快照；其他 spec 字段、风险、缓存、权限及确认绑定继续与原基线逐项比较。

## 执行记录

测试环境：既有 Python venv，APP_ENV=testing，测试专用数据库/JWT、不可达本地 DB/Redis 端口；临时目录中的合成文件。批准删除只在这些临时文件上验证，生产 Excel 从未作为删除目标。外部恢复记录为 mock，实际本地删除 Handler、目标解析、Registry/Policy/Dispatcher/Result 使用真实实现。

| 执行 | 结果与含义 | 原始证据 |
|---|---|---|
| 新 29 项对修复前 HEAD 的四个模块隔离加载 | 10 failed / 19 passed。失败包含 current 名称归一化、歧义吞掉、错误范围与新 schema 要求；不是 10 个独立生产事故，也不是整个旧应用回归。两个工作树均未被基线实验覆盖 | [命令](tool-unification-evidence/08-file-protocol/protocol-before-command.json)、[日志](tool-unification-evidence/08-file-protocol/protocol-before.log) |
| 27 个相关测试模块 | 1734 passed / 1 failed。唯一剩余失败是 Registry 旧断言仍要求模型 scope 可省略；按已批准新模型合同更新，并额外断言 legacy validation 仍无 required | [命令](tool-unification-evidence/08-file-protocol/protocol-test-command-233509.json)、[日志](tool-unification-evidence/08-file-protocol/protocol-tests-233509.log) |
| 失败模块与全部新增测试复验 | 127 passed，0 failed/skip/xfail；含新增 29 项。最终 1735 个不重复节点均有当前有效通过证据，不重复累加重跑次数 | [命令](tool-unification-evidence/08-file-protocol/protocol-test-command-233626.json)、[日志](tool-unification-evidence/08-file-protocol/protocol-tests-233626.log) |
| 静态差异 | git diff --check 通过；无生产配置、权限放宽、持久化格式或确认 schema 变化 | Git diff、被测源码指纹 |
| 原会话新增真实模型对照/候选复验 | 未验证，新增调用 0 次。自动审批拒绝超出先前 8 次的额度，已向用户申请明确的新增最多 24 次原会话 DashScope 授权；没有绕过拒绝 | 当前任务工具记录、外部修复方案 |

测试警告为 pytest-env 配置未识别、Pydantic class Config 弃用；本次环境通过显式 env 传入。首次本地迭代中的测试调用 ID 重复、错误的 validator 调用签名、旧协议/快照断言均已纠正；没有降低危险确认、唯一性或权限断言。

## 行为证据与闭环

| 验证项 | 状态 | 证据 |
|---|---|---|
| 完整中文日期名称→路径/ref/fid，current/workspace 一致 | 通过（本地） | `test_full_name_uses_existing_scoped_identity`；含原名与模型加空格反例 |
| 合法空格精确优先、同名目录及归一化碰撞返回候选 | 通过 | `test_literal_spaces_win_before_normalized_candidates`、`test_ambiguous_name_is_not_swallowed_into_keyword_search`；prepare 拒绝 0 Handler，直接 Handler 保留结构化歧义 |
| 明确错误路径不降为 basename、权限不扩大、漏 scope 不扫工作区 | 通过 | 新协议测试；既有 resource_scope_continuity、file_tool_boundaries、file_target_execution 的其他 owner/org/群、图片 read、定时动作范围测试 |
| 三入口搜索→引用→确认删除；拒绝/超时/目标变化 0 次删除，批准一次 | 通过（临时文件） | `test_name_search_then_reference_delete` 共 12 场景；实际删除与记录次数均断言 |
| 新 schema 与旧 execute/载荷、ID、模型循环及结果/回放兼容 | 通过（本地） | 新协议 29 项；07 定义/Registry、Result v0/v1、确认、产物、缓存与跨入口测试，详见命令清单 |
| 新模型说明是否稳定防止原会话改名/漏范围 | 未验证 | 需要新增原会话对照及候选真实模型验证；不能以确定性测试代替 |
| 最终部署版本的用户验收/受控关闭 | 未验证 | 当前修复尚未提交部署；不调用 accept-and-close |

技术问题 F08-05 保持未关闭；F08-03/F08-04 定时任务模型问题没有在本次修复。工具统一整体验收不能标通过。

## 后续验证与回退

授权后先完成方案内原会话说明对照，再按证据复核候选，固定候选代码指纹/提交，执行预先冻结的模型样本。只能调用现用 DashScope 和只读定位，不能自动执行删除。出现改名导致漏搜、错误目标或范围问题则保留失败，不能抽到成功后宣布修好。

模型及本地技术验证通过后，等用户“提交部署”指令通过受控入口发布。用户先用原 Excel 名字确认可找到，再选择可丢弃测试文件验证拒绝/批准删除；同时确认重名会给候选、错误会说明实际范围。对最终部署 SHA 明确验收后，仍需用户指令受控关闭。

没有数据库迁移。未部署时只需保留当前生产；发布后若需回退，走受控入口回到已推送候选，不直接覆盖生产说明。旧载荷可读不等于旧文件搜索缺陷已经不存在。

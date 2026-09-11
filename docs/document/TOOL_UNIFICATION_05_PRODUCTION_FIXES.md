# 板块 05 生产验收问题与修复记录

> 当前阶段：上下文机制已按用户后续授权恢复，包括无正文结果、缓存、逐调用状态和三条压缩路径；986 项相关测试通过、3 项原有 skip。见 [最新恢复及验收记录](TECH_05_上下文结果恢复.md)。本文件后文是此前诊断/候选历史；整体仍缺真实模型与用户生产复验，本轮未部署。

> 最新更正（2026-09-11）：下面是此前候选的历史快照。用户本轮已授权第 1、2 项；删除迁移与统一文件调用契约已完成，608 项当前自动化通过。原附件位置调整、额外目标提示已撤回，不再是当前改动；上下文/状态仅完成 Grok Build 和本项目历史对照。最新状态、实际修改、A/G 矩阵与证据以 [本轮修复与 Actor 对照](TOOL_UNIFICATION_05_ITEMS12_AND_ACTOR_COMPARISON.md) 为准。整块技术验收仍未通过，本轮未部署。下文的 731 项数字与位置调整仅供历史追溯。

## 范围、版本与当前结论

用户在 `887b28ae4aedb5d4dde5f0cbbd1bcb4484fda9ba` 完整部署后报告：任务删除失败；从工作区插入表格后读取报 RESOURCE_REFERENCE_INVALID；只要求读取新文件却继续了旧聊天中的统计/导出/画图目标。继续在原 05 工作树、原任务分支修复，没有启动 06。

**此前候选为 HEAD 887b28ae 加未提交修复，731 passed / 3 既有 skipped；本次生产故障版本仍是 887b28ae。当前技术验收未通过，完整方案仍缺实现和真实模型验证，不能直接进入发布验收。** 不把输入契约的自动化验证冒充真实模型行为验证。首次部署时前端 1309、后端 9277 passed / 37 skipped / 4 xfailed，与本轮修复测试分开计数。

## 事实与因果链

1. **删除的确定根因。** 23:08 的两次删除都在 ChangeSet confirm 内部失败，HTTP 200 返回的是失败 ChangeSet，数据库错误为 `scheduled_task_drafts_confirmed_task_id_fkey`。一个历史创建草稿仍引用待删除任务。244 的 confirmed_task_id 默认 NO ACTION；245 的 source_task_id 已是 CASCADE。249 的删除 RPC 正常执行 DELETE，却被遗漏的旧引用挡住。此次新创建任务成功，删除失败针对另一个已有任务，不能归因于本轮表单创建失败。
2. **引用的确定原因。** 23:10 首次 file_analyze 同时给出正确 fid 和 `resource_ref: "新表.xlsx"`。后者必须是 file_search 返回的签名引用，严格校验在 Handler 前拒绝。模型随后搜索并复制完整引用，分析成功。附件清单原来仅给 ID/名字/路径，工具 schema 另说明签名引用，但没有一个可直接复制的附件读取调用。
3. **上下文核验与尚未确定的部分。** 最新固定输入、资源清单、checkpoint 中最后一条用户消息都是“读取文件”及新附件；历史 revision 也正确。DashScope Adapter 原样透传消息，没有把旧任务重新作为当前任务派发。实际模型仍在读取成功后生成了额外汇总表和图。可复现的结构缺陷是当前附件短清单位于历史消息之前，旧的“这份文件”与新附件混处同一提示顺序；另有最新优先提醒但不足以避免这次误执行。该结构问题不能证明是模型偏离目标的唯一原因，本轮不宣称已根治所有语义偏离。

上述两处 prompt 源码及 244/245/249 迁移在 `6c0737ab` 和 `887b28ae` 逐字相同，证明缺口早于本次 05 发布；仍在当前用户验收任务内处理。未用“原有问题”豁免验收。

生产只读取部署后日志、该测试会话的消息/checkpoint/工具记录和指定 ChangeSet，数据库连接强制 `default_transaction_read_only=on`。原始记录保留在本机 `/private/tmp/tool05-production-investigation.log`、`/private/tmp/tool05-production-state.json`，权限 0600，不入 Git。仓库测试只用合成文件与数据，不调用外部模型、媒体、通知或业务 API。

## 修改与四个边界

| 边界 | 修复后关系 | 保留内容 |
|---|---|---|
| 模型 | 历史 → 最新请求边界 → 本轮附件摘要/XML → 原用户消息；raw 附件增加可复制的 read_call，仅含 file_id | 用户原话、显式“继续”语义、两种附件模式及 cache_control；原 Chat to_message_content / Loop to_tool_content、工具名和参数 schema 均不改 |
| 前端 | 沿用原 tool_step / emit / sink；本轮没有前端协议修改 | 原文件/图片/表单/ERP/媒体及 delivery goldens |
| 审计 | 沿用统一结果到原审计入口；253 不删除独立 ChangeSet / receipt 表 | 失败 ChangeSet 不冒充成功；删除幂等回执保留 |
| 持久化 | 253 将 confirmed_task_id 外键改为 CASCADE，与 source_task_id 的现有任务生命周期一致 | 旧 ToolResult 兼容投影、ledger/checkpoint 原格式完全不变；无新版回放 |

删除任务时会一并清理该任务的旧草稿及其 preflight 中间记录；这是补齐现有任务级联关系，不是提前清理生产数据。没有去掉外键、吞异常或放宽无效文件引用。原迁移文件不可变，修复通过新的 253 正向迁移完成。

## 逐项复验证据

| 编号 | 验证 | 当前状态 |
|---|---|---|
| A-05-01 | 新旧请求并存时检查真实 Adapter 出站 JSON：历史和当前输入顺序、两种附件模式、cache_control 开关；Chat/Loop 使用 read_call 后仍分别投影原结果 | 自动化通过；真实模型是否严格只读取待复验 |
| A-05-02 | 原 18 份 WS/delivery goldens、文件/图片/沙盒/ERP/媒体/表单消费用例原样通过 | 自动化通过；不声称本次重做人工观感检查 |
| A-05-03 | 正确附件 fid 在 Chat/Loop 均到达实际策略/解析/分发；错误 resource_ref 仍在 Handler 前拒绝；长产物、图片和表格去重旧消费用例通过 | 参数/产物自动化通过；用户报告的额外导出仍待新候选复验 |
| A-05-04 | 原 6 状态、异常/取消/uncertain 集成继续通过；删除 RPC 的运行中、版本冲突、跨组织拒绝通过 | 自动化通过 |
| A-05-05 | 原 ledger/checkpoint 回归、18 goldens 通过；真实数据库验证删除仅影响目标及其草稿，重复确认返回 duplicate，回执仅一条 | 自动化通过；新迁移未在生产执行 |
| G-01 | 本次用户验收修复仅两处上下文适配和一条关联外键迁移；无后续工具统一板块 | 通过 |
| G-02 | 删除负例/正例/重复/冲突/迁移回退，实际 Provider 出站协议、两消费者参数路径 | 自动化通过，真实模型行为待复验 |
| G-03 | 修复前新回归 6 failed / 1 passed；修复后 725 相关 + 6 PostgreSQL passed，0 failed/error/xfail；3 项既有 V1 gather skip | 通过；不把 skipped 计为通过 |
| G-04 | 旧 schema、模型结果、emit/sink、ledger 未改，API 删除仍经过原 ChangeSet | 通过 |
| G-05 | 本记录、CURRENT_ISSUES、05 验收记录及交接同步；明确迁移、回退限制及用户复验 | 通过 |

执行命令（无应用 .env，测试地址 `127.0.0.1:1`）：

```bash
TOOL_TEST_PYTHON=/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python \
PYTHONPATH=backend bash docs/document/tool-unification-evidence/run-05-fixes.sh
git diff --check
```

Python 3.12.12，完整已存在的 backend/venv；没有改共享依赖。[725 项回归日志](tool-unification-evidence/05-fix-regression.txt)、[6 项真实 PostgreSQL 日志](tool-unification-evidence/05-fix-postgres.txt)、[修复前复现](tool-unification-evidence/05-fix-baseline.txt)、[源码与环境证据](tool-unification-evidence/05-fix-source-checks.json)。新增测试共 17 项：8 个消息模式组合、2 消费者、1 无效引用、6 个 PostgreSQL 场景。

3 个旧 skip 是 test_chat_context.py 中已被 PromptBuilder 取代的 V1 gather 测试，源码/skip 未改。初期用共享 Python 3.14 环境时，34 个路由用例因先缺 python-multipart、后缺 supabase 无法加载；没有改断言或增加 skip。只临时安装了声明版本 python-multipart==0.0.21，随后切换到现有完整 Python 3.12 环境复验通过。中间日志保留本地 `/private/tmp/tool05-fix-regression.txt`，不把环境失败算通过。

PostgreSQL 测试自行创建 socket-only 临时集群，实际应用 069/244/245/249，再执行 253 和原 commit_scheduled_task_changeset；每例回滚，结束关闭并删除集群，不使用 DATABASE_URL。未安装 initdb/pg_ctl 的环境会明确 skip；本轮六例全部实际执行。

## 发布、用户复验与回退

- 等用户重新“提交部署”后，受控入口提交修复及执行 253；这轮不自行发布或代用户删除测试任务。
- 删除：在任务列表对之前失败的任务重新发起删除并确认，预期任务消失；旧失败 ChangeSet 仍保留失败事实，不自动重做。
- 附件：同一聊天保留早前统计/导出历史，插入另一张表，仅输入“读取文件”。预期第一次读取成功，回复结构/字段/必要样本，不新增汇总文件或图。然后明确要求“按刚才方式生成汇总表和图”，验证显式继续仍可执行。
- 上述生产复验目前均未执行。旧候选验收不覆盖修复，禁止关闭或进入 06。
- 应用代码可回到 `887b28ae`，新外键与旧代码兼容；这不会自动回滚 253。提供单独约束回退 SQL，真实 PostgreSQL 已验证恢复旧拒绝行为。已经由用户确认删除的任务/草稿不会由回退 SQL 恢复，不声称数据删除可逆。

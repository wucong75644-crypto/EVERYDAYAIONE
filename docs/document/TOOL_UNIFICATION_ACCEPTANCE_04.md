# 工具统一 04：技术验收记录

## 2026-09-10 发布全量门禁复验（提交前快照；最终状态见交付消息）

首个提交候选 ba3570936b9d43e15bf19ee28fbdb15ec9ebc294 已推送，但生产发布被后端全量测试拦下：9145 passed、4 failed、37 skipped、4 xfailed；前端 1309 项测试和构建通过且已部署，后端未同步，生产候选标记已失效。不能把首轮当作完整发布成功。技术结论等待修正后的受控发布全量复验；最终候选 SHA/状态以本次最终 RELEASE_RESULT 为准。

四项失败属于已批准行为对应的旧测试前提：三个附件显示测试只登记 /abs/report.xlsx，却请求上传目录中的另一个完整路径，依赖已禁止的 basename 回退；一个缓存版本测试仍要求 v3.0。三个 fixture 现登记与附件相同的完整路径并提供真实临时源文件，保留 analyzed/Parquet 断言，额外断言不同目录同名附件仍为 raw；版本精确要求 v3.1，未改为无约束判断。业务代码没有为通过测试恢复模糊别名或旧缓存。

定向复验命令（同本轮测试专用环境）：pytest test_attachment_routing_baseline.py、test_attachments_xml.py、test_file_scanners.py::TestV22Fixes::test_cache_schema_version_v3、test_file_target_execution.py，结果 114 passed、4 原有 xfailed。见 [初次全量失败](tool-unification-evidence/04-release-initial-failures.txt)、[复验](tool-unification-evidence/04-release-fixture-retest.txt)。A-04-06/G-03/G-04 补充采用此证据；下一次受控发布继续运行完整套件，不跳过失败测试。

附带环境记录：本机共享 Python 3.14 环境扩大运行整个 scanner 模块出现 13 项环境相关失败（含 Arrow 字符串算子不匹配、缺少 lxml）；这些用例均在同业务代码的发布 Python 3.12 隔离环境中通过。原始输出和逐项对照保留在 [本地 3.14 记录](tool-unification-evidence/04-release-local314-environment.txt)。本次没有修改共享依赖或 scanner 业务来掩盖差异，受控发布固定 Python 3.12 并安装项目 requirements。

原受控入口因保守策略保留 executor_unconfirmed 锁；已核验原本地 PID 停止、本地无发布执行器、远端无部署进程、候选标记不存在，使用原所有者和 release_owned_lock 恢复锁。没有盲目删锁、抢锁或把任务合入 main。任务分支/工作树保留。

---

## 2026-09-10 根因修复复验（当前有效状态）

**技术验收通过，用户已指令提交部署；用户验收未关闭。** NAS 不覆盖发布探针已于本次发布准备阶段通过并清理临时目录。此文档随候选提交；最终发布 SHA/部署状态由 RELEASE_RESULT 及交付消息记录。 本段覆盖下方所有历史状态。生产发布标记仍为 `20636929f7b78346991b357630240d932ed5772d`（preview，2026-09-09T16:01:26Z）。生产验证包括只读诊断、已有锁文件的 flock 检查和隔离临时目录的 no-clobber 探针，无业务数据写入/删除。

### 1. 范围与版本

- 基线及 HEAD：`20636929f7b78346991b357630240d932ed5772d`；原 01–03 稳定基座及祖先证据见历史记录，未复制其他任务修改。
- 分支：`codex/task/20260909225830-tool-unification-04`；执行目录：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-04`。
- 被测版本：HEAD 加当前未提交 backend 差异；逐文件 SHA256 见 [本轮源码检查](tool-unification-evidence/04-rootfix-source-checks.txt)。HEAD 不是新修复候选 SHA。
- 用户已授权 [文件目标解析与确认闭环方案](TECH_文件目标解析与确认闭环.md) 的完整根因修复。实际新增 file_resources、tools/file_calls、workspace_coordination；搜索/分析/删除/恢复共用规范目标，确认绑定内容版本，写入生命周期协调；补修缓存尾部指纹和任务短 ID 歧义。
- 允许的公共变化：可选 resource_ref(s)、restore record_id；旧输入仍接受；批量缺失/歧义整批未执行，恢复不覆盖。ToolResult/ledger/WS/数据库 schema 不变，不实施板块 05–07。新增协调适配覆盖可写沙盒，但不重写其业务能力、ERP 或内核执行协议。

### 2. 逐项证据

I = [test_tool_production_integration.py](../../backend/tests/test_tool_production_integration.py)；F = [test_file_target_execution.py](../../backend/tests/test_file_target_execution.py)。I 的 118 项真实入口配 mock Handler 仍全通过；F 的 64 项采用真实文件 Handler/临时文件，只 mock 身份数据库、OSS 与恢复记录外部写入。用例全名和结果在日志中可检索。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-04-01 | Chat/Actor、ToolLoop、旧 execute，新旧输入均走同一策略，无公共模型执行旁路 | 入口枚举和旧工具集成通过；F 五种文件选择参数 × 三入口实际删除各 1 次；完整 run 循环使用搜索结果 fref 执行；冷 Actor 恢复实际删除一次且无第二弹窗 | 通过 | I::test_all_entrypoints_new_and_legacy_reach_dispatcher_once；F::test_search_then_delete_real_handlers、test_complete_model_loop_carries_discovered_reference、test_cold_actor_resumes_signed_target_without_second_dialog；源码检查 |
| A-04-02 | 授权/action/范围/目标拒绝先于缓存、Handler、invocation | I 三入口拒绝与缓存/ledger trap 保持；F 缺失/歧义/plan/越界/目标变化均 Handler=0；不完整枚举、fid 碰撞和伪造引用拒绝；备份变更、invocation 拒绝不调用实际删除/恢复 | 通过 | I::test_denial_precedes_handler_cache_and_ledger、test_real_erp_action_route、test_scheduled_scope_intersection；F::test_bad_target_never_reaches_handler、test_incomplete_candidate_inventory_never_selects_first、test_colliding_legacy_fid_and_tampered_reference_are_not_authority、test_invocation_refusal_is_before_real_delete |
| A-04-03 | 真实确认拒绝/超时/异常/断连=0，批准=1；不得借用旧参数、范围或内容版本；不重复弹窗 | 真实 WS 等待链矩阵通过；不存在/歧义不发确认；批准等待中替换内容 Handler=0；冷 runtime + Actor 持久批准实际删除=1，无新请求；原资源通知与 ChangeSet 保留 | 通过 | I::test_real_confirmation_channel、test_approval_cannot_survive_scope_or_authorization_change、test_changed_arguments_get_new_confirmation_and_duplicate_is_single_use、test_proposals_and_resource_notices_do_not_add_confirmation；F 目标矩阵和 cold_actor 用例 |
| A-04-04 | 读 A/B 真重叠，写 C 等待两者，读 D 等写完成，写写不重叠 | Chat/Loop Event 轨迹仍通过；真实跨会话文件读写锁和双进程 IPC 通过；生产同机双进程共享/排他锁探针通过。无耗时阈值充当并发证据 | 通过 | I::test_real_read_overlap_and_write_barriers；F::test_cross_conversation_writer_waits_for_readers、test_cross_process_shared_and_exclusive_lock_protocol；[NAS 记录](tool-unification-evidence/04-rootfix-nas.md) |
| A-04-05 | actor/owner、预算/取消、Actor 恢复、幂等/安全点、实际存储发布语义 | 群真实删除只作用 owner；批准时预算耗尽 begin=0；等待锁取消 begin=0；批量删除取消停止剩余并保留已删记录；线程/OSS/内核收尾及原 lease/安全点通过。NAS no-clobber 发布及清理实测通过 | 通过 | F::test_group_actor_deletes_owner_resource_only、test_budget_expires_during_confirmation_before_invocation、test_cancel_between_batch_deletes_stops_remaining_and_records_completed、test_repeated_cancellation_drains_io_before_unlock、test_cancelled_oss_sync_drains_source_read_before_unlock；test_kernel_manager::test_cancel_waits_for_kernel_ack_and_preserves_state；[已验证探针](tool-unification-evidence/probe-04-nas-publication.py) |
| A-04-06 | 旧消费者/ledger 投影保持；相关回归通过，无半接入 | 四组 2904 passed，2 项原有真实 LLM/私有数据测试 skipped；无失败。原结果类/WS/serializer/fenced RPC/ERP 源码对照通过；内容缓存修复不改 ToolResult replay | 通过 | 下方四份最终日志；I replay/ledger/consumer 用例；F::test_csv_tail_change_rebuilds_real_conversion、test_analysis_uses_real_conversion_and_invalidates_source_cache；源码检查 |
| G-01 | 范围有据，无未说明契约/后续块改造 | 文件目标、恢复、缓存、短 ID 与必要 writer 协调均对应已批准方案；schema/sandbox 例外限定具体文件，其他协议/ERP树仍保持 | 通过 | 方案第 4/6 节；check-04-rootfix.py 保留全部未授权变化断言，仅按批准范围修正旧“整个业务文件不变”检查 |
| G-02 | 全 A 项成功/失败/边界证据闭合 | 本地用例和 A-04-05 NAS 发布实测已闭合，仍不代替用户业务验收 | 通过 | A 表；NAS 记录 |
| G-03 | 新增与受影响旧测试通过，不弱化正确断言 | 2904 passed；2 项既有 opt-in 真实 LLM 测试未执行并保留。初轮失败分类及替代验证见下方问题表；未新增 skip/xfail | 通过 | run-04-rootfix.sh 四组日志；修改前 41 失败日志及本轮复验 |
| G-04 | 工具名/旧参数/投影/WS 保持，新增参数明确 | 旧 files/file_ids/path 与新引用交叉验证；restore filename/record_id 二选一由 runtime 校验；冲突参数拒绝。Registry 深比较与结果、图片、表单、ERP TABLE 回归通过 | 通过 | test_tool_registry/test_file_tools/test_chat_tools/test_file_id_protocol/test_file_handles_e2e/test_tool_result/test_ws_tool_confirmation；源码检查 |
| G-05 | 接口/入口/证据/限制/回退可交接 | HANDOFF、方案和 CURRENT_ISSUES 更新；不冒充生产完成，保留 NAS/历史备份限制及新候选验收步骤 | 通过 | 本记录及交接 |

所有生产模型入口与已消除旁路见下方历史入口表，路径仍由本轮 AST/调用点检查重新核验。当前文件链在该表的 Runtime 内增添 resolve → prepare → confirm → guarded verify；旧 execute 与 Actor/ToolLoop 没有独立文件执行兜底。

补充实际源文件写入覆盖：Runtime(file_delete/restore/code_execute)、FileExecutor(write/edit/delete/mkdir/rename/move)、api/routes/file_upload 的两条上传、image 的 NAS 上传、services/file_upload.download_url_to_workspace、WecomFileMixin._prepare_wecom_file、SandboxExecutor.execute；ScheduledTaskAgent 模板读持共享锁。OSSService 的 workspace 同步/删除/缩略图线程 IO 排空后才释放上层协调。staging 输出不作为可删除源文件；ERP/媒体内部业务编排未改。

### 3. 命令、环境与结果

```sh
PYTHONPATH=/private/tmp/tool04-testdeps:backend bash docs/document/tool-unification-evidence/run-04-rootfix.sh
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/check-04-rootfix.py > docs/document/tool-unification-evidence/04-rootfix-source-checks.txt
```

运行器拒绝工作树存在 .env/backend/.env，设置测试专用 APP_ENV、127.0.0.1:1 数据库、Redis 端口 1 和占位 JWT；不加载生产配置。Python 3.14.2 / pytest 9.0.3。项目已声明 time-machine==2.14.1，本机 venv 缺少它，本次仅安装到 /private/tmp/tool04-testdeps 作为测试路径，没有修改共享 venv 或项目依赖。

| 测试组 | 实际最终结果 | 日志 |
|---|---|---|
| Registry/Policy/Dispatcher/Result + 真实生产入口 | 858 passed | [core](tool-unification-evidence/04-rootfix-core.txt) |
| 原执行器/循环/确认/权限/Actor/文件/ERP | 1613 passed | [regression](tool-unification-evidence/04-rootfix-regression.txt) |
| ERP 单独进程（原 collection stub 隔离） | 11 passed | [erp](tool-unification-evidence/04-rootfix-erp.txt) |
| 新文件根因链 + 上传/源缓存/Wecom/OSS/内核/任务 | 422 passed，2 skipped | [files](tool-unification-evidence/04-rootfix-files.txt) |

合计 2904 passed、0 failed/error/xfail、2 skipped。两项跳过来自原 test_file_analyze_integration 的 RUN_LLM_INTEGRATION=1 和私有真实 Excel fixture 门槛，不是本轮新增；不计通过，不冒充真实 LLM 验证。当前要求的缓存正确性已有真实 CSV/Parquet/源快照替代证据；Excel 原解析/扫描链未重写。日志含既有 pytest env 配置、Pydantic/FastAPI 弃用警告，未影响行为断言。

### 4. 问题与复验

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| F-01/02 | 明确路径被热缓存改写；冷缓存 fid 无法定位 | 名称/路径/短 ID 与进程缓存混用 | 04 | 统一 scoped resolver；明确路径不回退；冷范围枚举及新签名引用 | F explicit_path、cold_reference、五参数三入口、cold_actor |
| F-03/04 | ghost 清单仍有 fid；含竖线文件名被截断 | 从展示文本重建身份，未核实实体 | 04 | 结构化 hits 直接生成定位字段；缺失不可用且无引用 | F missing_manifest、search_then_delete |
| F-05 | 批准等待期替换文件仍进入 Handler | 批准绑定路径而未绑定版本，写入无共同协调 | 04 | 全内容/版本绑定 + guarded execution + writer 协调；取消排空 | F changed 矩阵及生命周期/IPC；NAS 发布已实测 |
| F-06 | 短 ID 多匹配取首条 | ID 歧义与名称候选处理不一致 | 04 | 显示完整候选；不提交提案 | F ambiguous_scheduled_id 四个 action；test_chat_task_manager |
| F-07 | 恢复覆盖当前文件，同名记录取最新 | 二次模糊查询与无条件目的地写入 | 04 | 固定 record_id/ETag；条件下载；不覆盖发布 | F restore_no_clobber、deleted_record_ambiguity、changed_backup；NAS 发布已实测 |
| F-08 | CSV 1MiB 后变化仍读旧 Parquet | 缓存指纹只覆盖源前缀 | 04 | 全 SHA256 / v3.1 / 稳定快照；映射随源版本失效 | F csv_tail、analysis_uses_real_conversion、source_snapshot_rejects_change |
| V-01 | 初轮 41 failed / 973 passed | 真实目标前置使旧 mock 数据不足；旧 schema/跳过缺失行为断言过时；缺 time-machine；另有显示别名兼容回归 | 04 | 补真实文件/备份、结构化 hits、签名测试 key；保留原 raw 记录语义；补临时依赖；完整复验通过 | [初轮](tool-unification-evidence/04-rootfix-initial-files.txt)、最终四组 |
| V-02 | 扩大原回归初轮 4 failed / 1609 passed | restore required(filename) 不适合 record_id；manifest 假守卫抛 FileNotFound；两个 mock settings 无字符串签名 key | 04 | 恢复参数仍需二选一；manifest 断言加强为缺失无引用；fixture 提供占位签名 key；1613 全通过 | test_chat_tools/test_resource_manifest/test_file_handles_e2e；regression 日志 |
| V-03 | 自审两项先复现失败 | CSV/TSV 同字节配置未进入缓存键；初版修复在锁外重绑共享缓存 metadata | 04 | 分隔符入 key；源路径只绑定本次视图，共享 metadata 不重写 | [修改前两项失败](tool-unification-evidence/04-rootfix-cache-review-before.txt)、最终 F 两项及 [受影响缓存复验](tool-unification-evidence/04-rootfix-final-cache.txt) |
| P-01 | 当前 NFS local_lock=all | 只保证同机多进程，不能跨主机宣称互斥 | 04 | 当前 Web/Actor 同机 root，双进程探针通过；多主机扩展是明确部署前置 | NAS 记录 |
| P-02 | 发布准备需补齐 NAS no-clobber 实测 | 此前仅本地 link 通过 | 04 | 用户提交部署后执行隔离临时目录探针，两条 PASS，目录已清理；已关闭 | probe-04-nas-publication.py、04-rootfix-nas.md |

旧断言变更依据：批量缺失时过去删除其余文件，现按用户已批准行为要求整批 Handler=0，并另测合法去重只删一次；旧 schema required(files/filename) 改为允许替代选择字段，但 runtime 对无参数/错误类型/冲突仍拒绝；先确认才发现不存在的 mock 场景补真实目标，不削弱批准/拒绝/调用次数断言。原业务记录 raw 标签回归已在代码中恢复，没有改测试掩盖。检查脚本按当前路径处理中文文件名，原协议/ERP/Actor 不变断言仍有效。

### 5. 用户验证单

部署后记录**新的确定 SHA**（当前 20636929 不含修复），在获准的临时测试工作区执行：

1. 上传测试 CSV 到子目录，用完整名/省略扩展名/片段搜索；返回正确完整路径。两个目录放同名文件时应要求选择。
2. 要求删除不存在的名字，不应弹删除确认；删除测试文件时，拒绝后文件仍在，批准后仅正确目标消失。
3. plan 模式要求删除，Handler 不执行；确认等待中替换测试文件后批准，应返回资源变化而不删除替换版本。
4. 定时任务仅授权允许的只读工具，观察正常读和范围外请求拒绝；不因无 UI 放行危险调用。
5. 获准恢复测试时，指定删除记录；目的地已有文件明确冲突，不覆盖；选择无冲突记录可恢复。

真实 ERP 写入、生产业务文件删除、付费生成和真实备份变更均未获本轮授权，不执行。NAS 临时探针不代替这些用户验收。

### 6. 结论与交接

最终自审按项目 Review 技能复用现有差异与证据，核对信任边界、真实入口、文件/OSS/内核取消、缓存配置和旧协议；缓存配置与共享 metadata 两项发现已复现并修复。未发现其他高置信未修复的本轮代码问题；这是同任务自审，不冒充独立代理或生产业务验收。NAS 发布探针随后已通过，当前无未关闭的本轮技术阻塞项。

本轮实现与本地回归已完成，**P-02 存储实测已补齐，技术验收通过，待确定候选部署及用户验收**。原 deleted_files 没有删除当时的不可变备份版本，本次只绑定准备时 ETag，不承诺修复历史已覆盖备份。文件/OSS/数据库保留原失败语义，无零丢失或跨系统事务承诺。

回退本次根因修复可回到 20636929，但也恢复 F-01～08 已知缺陷；不称已修复方案。结果/ledger/schema 无迁移，旧输入保留；换版本/密钥旧批准不匹配则重新确认，新 fref 在旧版本不保证可用。完整撤销 04 的原稳定基座仍是 0f65d72d。均不自动执行回退、部署或关闭。

下一步：执行本次已授权的提交部署 → 新 SHA 用户验证 → 明确验收关闭并受控合入 main。**在此之前不进入 05。** [交接](TOOL_UNIFICATION_HANDOFF.md) 和 [实施方案](TECH_文件目标解析与确认闭环.md) 已同步。

---


## 2026-09-10 修复前诊断增量（历史状态，已被上文覆盖）

**技术验收未通过；已部署，用户验收未关闭。** 本段覆盖后文发布前的状态结论，保留后文测试记录作为历史证据。当前 HEAD/上轮已核验部署候选为 `20636929f7b78346991b357630240d932ed5772d`，分支和工作树不变。本次未提交差异仅为诊断方案、复现脚本/日志和文档更新，无业务代码改动或再次部署。

用户生产验证发现按名称/片段搜索后删除失败。上轮只读生产核查证明存在猜根路径先确认后未找到的链路，同时正确 keyword→fid→批准删除已有成功记录。这次在同一候选代码上扩大相邻逻辑检查，复现 8 项缺陷，见 [方案与问题清单 F-01～08](TECH_文件目标解析与确认闭环.md)。不把代码证据或临时目录实验称为生产误操作。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-04-03 增补 | 确认对象在等待期间被替换，旧批准不能执行替换后的对象 | 同路径内容替换后，真实 execute 调用 mock Handler 一次 | 未通过 | [脚本 confirmation_file_version](tool-unification-evidence/04-target-audit.py)、[日志](tool-unification-evidence/04-target-audit.txt) |
| A-04-05 增补 | 冷 worker/Actor 恢复时保持已发现文件可定位且安全 | 冷缓存模拟返回 resource_id_unavailable；完整 Actor 文件引用恢复与拟议修复尚待集成验证 | 未验证 | 同脚本 cold_worker_fid；现有安全拒绝不代表恢复可用性通过 |
| G-02 增补 | 必需行为无失败项 | A-04-03 新发现目标绑定缺口，尚未修复 | 未通过 | 同上；不能沿用后文总 passed 数关闭 |
| G-03 增补 | 修复失败场景并运行受影响回归 | 当前只执行缺陷复现；业务修复及受影响回归尚未执行 | 未验证 | 用户本轮仅要求方案；草稿第 5 节列出复验矩阵 |
| G-05 增补 | 当前状态、接口建议、限制、证据可交接 | 已更新本记录和 HANDOFF，草稿明确未实施和并发保证前提 | 通过 | [方案草稿](TECH_文件目标解析与确认闭环.md) |

其余 A/G 项保留原测试的历史结果，本次没有重跑，也不以此宣布修复版本通过。修复候选必须按全部 A-04-01～06、G-01～05 重新汇总受影响证据。F-01～08 均待修复/复验；恢复同名最新记录和二次查询风险另有代码证据，尚未声称生产发生。

本地执行目录为本任务工作树，使用测试专用数据库/JWT 配置和隔离临时文件：

```sh
APP_ENV=testing DATABASE_URL=postgresql://test:test@127.0.0.1:1/test JWT_SECRET_KEY=local-audit-test-only-key-long-enough REDIS_PORT=1 PYTHONPATH=backend /Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/04-target-audit.py
```

结果：8 项预期缺陷全部复现，退出 0，非 pytest 修复验收。第一次诊断脚本因 str/Path 类型写法报错，改脚本后重跑完整成功；业务代码未变。Arrow 读取 CPU 信息的 sysctl 警告未影响数据断言。身份 DB、确认和 OSS 为 mock；CSV→Parquet 转换、文件搜索和恢复目的地写入使用真实业务代码，但所有文件在临时目录。无生产密钥、用户文件或原始生产聊天记录进入本证据包。

后续用户验证需在获准测试资源上重复名称/片段搜索、确认拒绝/批准、plan 拒写、定时范围，并记录新的部署 SHA；本次未获真实写入授权，生产副作用验证仍待执行。当前不允许关闭板块 04 或进入板块 05。

---

以下为发布前历史记录。

## 1. 范围与版本

状态：**技术验收通过，待部署/用户验收**。聊天/Actor、定时/预检 ToolLoop 和旧 `ToolExecutor.execute` 已完整接入 Registry/Policy/Dispatcher。这里只报告真实生产代码配 mock 业务 Handler 的本地验证；没有执行真实删除、ERP 写入或付费生成，没有发布生产。

| 项目 | 记录 |
|---|---|
| 基准提交 | `0f65d72dd00a0fce6885d4df0b7977454f666812`，受控 task-worktree start 从最新 origin/main 创建 |
| 当前分支 | `codex/task/20260909225830-tool-unification-04` |
| 当前 HEAD | 同基准；尚不包含本块未提交实现，不能作为已测试候选 SHA |
| 工作树 / 执行目录 | `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-04` |
| 前置证据 | 用户确认 01–03 已验收进入 main；核验 01 `b4c854ac`、02 `4084db4e`、03 `e243ba2c` 及附件/文件边界修复 `2ed4d783` 均为基准祖先。基准与 03 最终候选 `2ed4d783` 的 tree 均为 `c38a4f18c3ec0ae8193af3c7b9750877df6ae933`。03 文档中的未发布状态是其当时快照，不能否定当前 Git 及用户验收事实 |
| 被测版本 | 基准加本任务未提交的 backend 差异；逐文件 SHA256、实际构造点、业务/协议对照见 [04-source-checks.txt](tool-unification-evidence/04-source-checks.txt) |
| 文档与证据 | 本记录、HANDOFF 的 04 增量、run-04.sh、check-04.py、三份最终测试日志及 fixture 初轮复现日志 |
| 发布候选 | 未提交、未推送、未部署、未合并、未清理。待用户“提交部署”后记录确定候选 SHA，并核对其源码与上述指纹 |

实际改动对应范围：

- `tools/runtime.py`、`runtime_context.py` 装配可信身份、模式/域、资源、授权、确认、预算/取消及统一执行；`tools/execution.py` 增加只在 Policy allow 后运行的兼容缓存/ledger hooks；`policy.py` 保留旧授权名单上界并拒绝失效版本。
- Chat 的 stream_setup/tool_loop/execution_engine、ChatToolMixin 和 ExecutionScope 接入；`chat/tool_lifecycle.py` 适配已有 Actor invocation，`tool_invocation_store.py` 只增加旧表的只读 lookup；stream_loop 清理请求运行引用。
- ToolExecutor 旧门面、ToolLoopExecutor/helpers、ScheduledTaskAgent、scheduled_task_workflow 统一执行和核心/动态展示；FileExecutor 增加不创建目录的检查构造选项，默认行为不变。
- WS 确认响应只接受字面布尔值 `True`，不再把字符串 `"false"` 转成批准。WS 字段、builder、前端界面和结果 content blocks 不变。
- 新增集成测试及可信测试 DB/Handler 支撑，更新受影响的旧 fixture/过期断言。没有数据库迁移、新结果 reader/writer、业务 Handler 重写、ERP 内部编排或板块 05–07 改造。

## 2. 逐项证据表

下表 `I::` 指 [test_tool_production_integration.py](../../backend/tests/test_tool_production_integration.py)，`E::` 指 [test_tool_execution.py](../../backend/tests/test_tool_execution.py)，`T::` 指 [test_tool_result.py](../../backend/tests/test_tool_result.py)。I 中所有模型/外部业务 IO 为 mock；真实 Registry、Policy、Dispatcher、兼容出口、Chat/ToolLoop 调度、WS 确认等待和旧 ledger 序列化参与执行。准确命令及文件集合见 [run-04.sh](tool-unification-evidence/run-04.sh)。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-04-01 | 所有模型工具生产入口的新旧工具都经同一策略，无公共 execute 旁路 | legacy/chat/loop × explicit file_search/legacy web_search 的 Handler 与 Dispatcher 均为 1；拒绝矩阵覆盖三个入口。共享 Chat `_run_loop` 在普通/Actor × auto/plan 四种组合实际装配 ToolExecutor；ScheduledTaskAgent 与真实 ToolLoop 串联。生产构造点仅 ChatToolMixin、ScheduledTaskAgent，ToolExecutionService 仅由 ToolRuntime 构造 | 通过 | I::test_all_entrypoints_new_and_legacy_reach_dispatcher_once；I::test_shared_chat_engine_passes_mode_and_retains_safe_points；I::test_scheduled_agent_real_assembly_and_loop；[调用点检查](tool-unification-evidence/04-source-checks.txt)；下方入口清单 |
| A-04-02 | 无权限、plan 写/生成、跨用户/组织/域、越出定时范围、ERP query 写 action 在 Handler/cache/invocation 前拒绝 | 三入口十种拒绝场景均 0 Handler；另以 trap 验证 cache.get、replay、begin 均未访问。实时组织状态、PermissionChecker、授权版本/名单、功能开关均参与。ERP 读/批准写为 1，query 写和未知 category 为 0；旧 execute 的交互授权快照仍是上界。恢复文件的外部目标和旧缓存撤权也拒绝 | 通过 | I::test_denial_precedes_handler_cache_and_ledger；I::test_business_permission_checker_denies_before_handler；I::test_declared_permission_is_loaded_without_trusting_a_prior_boolean；I::test_scheduled_scope_intersection；I::test_real_erp_action_route；I::test_legacy_snapshot_is_still_an_upper_bound_in_interactive_mode；I::test_cache_cannot_survive_revoked_membership；I::test_restore_record_with_foreign_destination_is_rejected_before_handler |
| A-04-03 | 未确认/拒绝/超时/异常/断连为 0，批准为 1；不重复确认，不能借用旧参数/范围的批准 | 三入口均用真实 WebSocketManager.wait_for_confirm/resolve_confirm：批准 1，其余 0，等待器全部清理。取消确认时 0。批准后重新读取 mode/owner/org/membership/allowed_tools/manifest；任一改变均阻止业务。参数变化生成不同绑定，同 call ID 不重复 dispatch。Actor 恢复匹配的持久批准时无新弹窗、只执行 1；资源通知及任务提案无附加弹窗 | 通过 | I::test_real_confirmation_channel；I::test_cancellation_propagates[confirm]；I::test_approval_cannot_survive_scope_or_authorization_change；I::test_changed_arguments_get_new_confirmation_and_duplicate_is_single_use；I::test_actor_durable_approval_bound_to_arguments_and_owner；I::test_manifest_revocation_after_confirmation_precedes_invocation；I::test_proposals_and_resource_notices_do_not_add_confirmation；旧 test_tool_confirm/test_ws_tool_confirmation |
| A-04-04 | 真实读 A/B 重叠；写 C 等两者结束；D 等 C；写写不重叠 | Chat/ToolLoop 两入口：A/B 都到达 Event 屏障且未释放时只有两条 start；释放后 C 的 start 位于 A/B 两条 end 之后，D 在 C end 之后，E 在 D end 之后；C/E 开始时 running 集合均只有自身。模型调用顺序保留，不再按随机 call ID 排序。取消同批一个读时另一读被取消并 drain，后续写不开始 | 通过 | I::test_real_read_overlap_and_write_barriers；I::test_real_tool_loop_run_uses_model_order；I::test_batch_cancellation_stops_sibling_reads_and_skips_write；旧 test_tool_loop_parallel。断言基于事件/轨迹；2 秒 wait_for 仅防测试死锁，不作为性能阈值 |
| A-04-05 | actor/owner 隔离、预算/取消、Actor 确认恢复；锁、幂等和安全点保留 | 群 owner 与真实 actor 分离，个人工具不可见/不可执行，当前 channel scope 改变后 0 Handler；预算耗尽在 DB/Handler 前中止，确认/业务/批次取消继续传播。恢复使用当前 manifest 解析 fid，原缓存为空也能安全回放；过期成员或外部产物拒绝且不重执行业务。invocation execute/in_progress/uncertain/ownership_lost 的 Handler 次数为 1/0/0/0；原 fenced RPC 与 safe_point 调用 AST 一致 | 通过 | I::test_channel_actor_owner_and_personal_tool_isolation；I::test_budget_exhausted_precedes_everything；I::test_cancellation_propagates；I::test_invocation_gate_and_legacy_completion；I::test_restored_file_id_uses_manifest_and_preserves_legacy_replay；I::test_replay_current_permissions_without_business_or_confirmation；I::test_replay_foreign_artifact_denied_without_business；旧 test_execution_scope/test_chat_execution_engine/test_model_gateway_concurrency_integration；[源码对照](tool-unification-evidence/04-source-checks.txt) |
| A-04-06 | 仍经兼容投影进入旧消费者/ledger；相关旧执行器/循环/确认/权限测试通过，无半接入入口 | 原 AgentResult/ToolOutput、FileReadResult、Form/str 投影及 WS 消费测试通过。Actor ledger 仍写旧 kind=agent_result 等；返回业务 error 仍记调用 succeeded，Handler 异常记 uncertain；拒绝/回放不登记新执行。投递/完成写入/缓存写入故障不重做业务，不把已完成业务误报为未开始。三组最终 2482 passed，无失败/跳过/xfail | 通过 | I::test_invocation_gate_and_legacy_completion；I::test_handler_uncertain_is_separate_from_delivery_failure；I::test_cache_write_failure_preserves_completed_business_and_single_use；I::test_chat_reuses_request_service_across_model_rounds；T 全部；[核心](tool-unification-evidence/04-core.txt)、[回归](tool-unification-evidence/04-regression.txt)、[ERP](tool-unification-evidence/04-erp.txt) |
| G-01 | 实际变化对应 04；无未说明公共契约变化或业务重写 | 本节范围与下方入口/旁路表对应；原 ToolExecutor 业务方法 AST、FileExecutor 除构造器外方法 AST、ERP 内部目录、媒体/沙盒业务、配置 schema、前端/WS builder 均无差异。新许可外壳一次完整接入，未实施结果展示或新 replay 协议 | 通过 | 第 1 节；[源码结构与指纹](tool-unification-evidence/04-source-checks.txt) |
| G-02 | A-04-01～06 每项含成功/拒绝/失败和必要边界 | 上述六项均有对应生产入口集成及最终日志；不是仅测新类或仅看分批数组。未知授权、撤权、确认故障、群隔离、生命周期均有 0/1 调用证据 | 通过 | 上述 A 表；[04-core.txt](tool-unification-evidence/04-core.txt) 中 I 的 118 个用例 |
| G-03 | 当前新增/相关旧测试通过；正确旧断言不删除/跳过/弱化 | 858 核心/集成 + 1613 回归 + 11 ERP = 2482 passed，0 failed/error/skipped/xfail。改为真实 runtime 的旧 fixture 和必需目标冲突断言说明见问题清单。共享网关 fixture 初轮 2 failed 已定位并最终复验；没有待处理的本块技术失败 | 通过 | [执行脚本](tool-unification-evidence/run-04.sh)、三份最终日志、[fixture 初轮](tool-unification-evidence/04-fixture-initial.txt)；第 4 节 |
| G-04 | 工具名/schema/合法参数别名、旧 API/返回、WS 对照通过 | Registry 深比较原 schema/别名，业务方法未改。旧 execute 原位置与参数保留，新增运行事实参数及 keyword-only call_id；安全拒绝仍通过兼容 PermissionError/旧消费者呈现。file_ids/files/path 先转稳定目标再传原 Handler；旧文件/图片/表单/ERP TABLE 对照全通过。确认 id 仍占用原不透明 tool_call_id 字段，值绑定参数/范围，批准必须为 bool | 通过 | test_tool_registry/test_tool_execution/test_tool_result/test_file_id_protocol/test_file_handles_e2e/test_chat_generate_mixin/test_tool_loop_tooloutput/test_ws_tool_confirmation；源码对照与下方兼容说明 |
| G-05 | 接口/调用点/证据/限制/回退及下一块前置可交接 | HANDOFF 更新当前 main 前置与 04 实际接口。本块没有新 payload/schema，原 serializer/deserializer 与 reader 不变；回退只撤销相对 0f65d72d 的 04 增量，保留 03 附件修复。未部署、用户验证未完成，05 只具备代码前置，不可启动 | 通过 | [交接](TOOL_UNIFICATION_HANDOFF.md#板块-04-实际接入)、第 5/6 节 |

### 生产入口与已消除旁路

| 入口 | 当前真实调用链 / 可信事实来源 | 消除的旁路 |
|---|---|---|
| Web 聊天 | run_legacy_chat_stream → execute_chat/_run_loop → ChatToolMixin._execute_tool_calls/_execute_single_tool → ToolRuntime.execute → ToolExecutionService → Dispatcher → 原 Legacy Handler；身份来自服务端请求/会话，mode/domain 从本轮 prepared context 显式传递 | 原只按 SafetyLevel 分组和工具名单检查后直接 execute；执行路径与确认路径分离导致的遗漏 |
| Actor 聊天（含群） | ChatGenerationExecutor.execute → 同一个 execute_chat/_run_loop；已解析 ExecutionScope 的 actor、owner、channel_scope_id + ContextAnchor/ResourceManifest + Actor budget/cancel/token；同一 runtime 加 ActorToolLifecycle | 先登记 invocation 再补检查；回放先返回旧数据再检查当前资源；确认恢复借用未绑定参数/范围的 ID；投递异常误记 uncertain |
| 定时运行 / 预检 | ScheduledTaskAgent.execute → _build_tool_loop → ToolLoopExecutor.run/_execute_tools → invoke_tool_with_cache → 同一 runtime/service/dispatcher；DB task 的 actor/owner、execution_policy/原 tool_policy_snapshot、scheduled/preflight、auto/general、预算/取消显式装配 | cache 命中先于授权；无 UI 或确认服务异常时自动放行；工具名授权扩大成危险参数授权；按随机 call ID 重排；复制模板前不复核当前身份 |
| 旧 execute / re-export | services.tool_executor 仍导出 services.agent.tool_executor.ToolExecutor；execute(name,args,call_id=...) → runtime → to_legacy | 公共 execute 直接 `_handlers.get` 调用；仅凭 allowed_tools 或旧 preflight 手写名单检查即可执行；interactive 快照绕过 |
| 核心 / 动态发现 / 定时规划展示 | stream_setup._prepare_permission_and_tools、chat.tool_loop.prepare_tool_turn、ToolRuntime.advertised、ToolLoop 的动态扩展、scheduled_task_workflow 的 preflight/plan schemas 均取 Registry.resolve/Spec | 核心/动态列表另走 get_tools_by_names/filter 或 CapabilityRegistry；发现名称直接扩权。旧 catalog helper 只用于元数据兼容，不发执行许可 |

源码检查枚举 services/api 的全部 ToolExecutor、ToolLoopExecutor、ToolExecutionService 构造点和分发调用。Dispatcher 的唯一工具生产调用者是 ToolExecutionService；LegacyToolHandler 只绑定原私有 `_handlers`。剩余 scheduler.chat_task_manager/ERP 内部 action 分发属于原业务实现，不接收模型工具调用，也不构成公共模型执行入口。旧 partition_tool_calls、validate_runtime_tool、ToolLoop._request_user_confirm 保留兼容定义，但生产链已无调用；不存在拒绝后的旧执行兜底。

### 执行顺序与兼容边界

1. 静态 Registry/模式/域/action 拒绝 → 模型身份覆盖检查 → 当前组织/成员/会话与声明业务权限读取 → 刷新资源清单 → 规范化路径/ID 和资源检查 → Policy。
2. Actor 只读 lookup 在当前权限/资源允许后发生；已完成记录校验名称、参数哈希和显式 workspace 产物范围，使用原 reader 回放，0 Handler、0 新 begin。运行中/uncertain 或参数不符拒绝，不盲目重试。
3. 需要真实确认时走既有 WS/Actor 命令；确认后重新构造当前 mode/domain/actor/owner/org/授权/feature/manifest 和目标，再由同一 Policy 核对绑定。资源消耗 CONFIRM 仍是通知，任务管理仍交原提案/表单机制。
4. 最终 allow 后，请求服务预占 call ID → 允许的旧缓存读取 → 原 Actor begin/fencing → 原 Handler → 旧对象兼容投影和旧 ledger completion → 原消费者/审计。取消穿透；同批取消清理其他读任务。

确认等待期间文件名/缓存别名不能改换目标：危险文件参数先解析成稳定绝对路径。不同参数、资源清单或作用域不能复用批准。服务跨同一聊天请求的模型轮次复用，结束时清理；Actor 重启依靠原 invocation 和已绑定的持久确认，不新增永久内存幂等系统。

未更改 code_execute 的原缓存资格、restore_file 的原 safe 风险及 invocation 资格、业务锁/重试/退款、ERP 查询 execute_raw 保护或结果展示。旧 ledger payload 结构及序列化完全相同；只增加授权后的 read-only lookup。跨版本旧批准 ID 无新绑定，不能当作新批准；旧 invocation 如规范化参数哈希不一致会明确拒绝，不迁移/改写记录也不重跑业务。此为安全恢复行为，不能据此宣称已经完成板块 06 的 replay 协议改造。

## 3. 测试环境与摘要

在第 1 节工作树执行：

```bash
bash docs/document/tool-unification-evidence/run-04.sh
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/check-04.py > docs/document/tool-unification-evidence/04-source-checks.txt
```

Python 3.14.2、pytest 9.0.3；脚本拒绝存在 `.env` 或 `backend/.env` 的测试工作树。显式 APP_ENV=testing、占位 PostgreSQL `127.0.0.1:1`、Redis 端口 1、测试 JWT；不读取生产配置。所有 pytest 使用 `-o addopts='' -v --tb=short`；没有增加 skip/xfail 或覆盖率门槛。

| 组 | 实际结果 | 日志 |
|---|---|---|
| 新生产接入 I + 03 执行/结果 + 01/02 Registry/Policy | 858 passed，2 warnings | [04-core.txt](tool-unification-evidence/04-core.txt) |
| 相关执行器/Chat/Actor/循环/确认/权限/资源/文件/任务/网关/ERP 旧回归 | 1613 passed，4 warnings | [04-regression.txt](tool-unification-evidence/04-regression.txt) |
| 既有 ERP Mixin，独立进程 | 11 passed，2 warnings | [04-erp.txt](tool-unification-evidence/04-erp.txt) |
| 总计 | **2482 passed，0 failed/error/skipped/xfail** | 三组最终命令均 exit 0 |

警告保留：pytest-env 未安装导致 env 配置项未知（脚本显式设置，不依赖插件）；KIE Pydantic class Config 弃用；FastAPI/Starlette asyncio.iscoroutinefunction 弃用。ERP Mixin 的收集期 sys.modules 污染沿用 01 的已复现依据，继续独立进程执行；该文件未改，未跳过测试。

结构检查包括全部变更 Python AST、git diff --check、脚本语法、生产调用点、原业务方法及持久化/WS/安全点源码对照和测试代码指纹。未运行全仓测试；已按实际受影响调用链运行上述相关回归。没有真实 ERP、数据库权限撤销、浏览器生产确认、删除/付费操作的实测结论。

## 4. 问题清单

本块未处理的技术问题：**无**。真实生产验证仍为“未验证”，见下一节，不计入本地 mock 测试成功。

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P-04-01 | 旧无 task/确认异常单测断言自动返回 True | 旧 ToolLoop 确认失败放行，与 A-04-03 直接冲突 | 04 | 生产确认收口；保留 helper 兼容但 fail closed，旧断言改为 False；三入口真实确认故障 0 Handler 替代验证 | test_tool_confirm；I::test_real_confirmation_channel；最终核心/回归日志 |
| P-04-02 | 01–03 的源码测试要求生产文件绝不 import services.tools；旧 helper 测试使用已不存在的 data_query | 前者仅适用于基础隔离阶段，与 A-04-01 的生产接入冲突；后者非当前工具符号 | 04 测试 | 源码断言改为生产接入存在、目录无循环依赖；data_query 换为当前 search_knowledge，保留动态发现去重/域/退出行为断言 | test_tool_policy/test_tool_registry/test_tool_loop_helpers；source-checks；最终日志 |
| P-04-03 | 旧 executor/循环 fixture 只替换 execute 或提供 MagicMock 身份，无法覆盖新可信入口 | fixture 绕过要验的 Policy/Dispatcher，或缺真实上下文事实；不是扩大运行权限的理由 | 04 测试 | 使用真实 ToolExecutor/runtime + mock 当前身份 DB/业务 Handler；补 mode/domain/budget/cancel/snapshot 和认证批准 user_id；保留返回、计费、图片、表单、终止及审计断言 | 受影响 test_tool_executor/test_chat_tool_mixin/test_tool_loop_parallel/test_scheduled_task_agent 等；最终回归日志 |
| P-04-04 | [初轮两项共享网关失败](tool-unification-evidence/04-fixture-initial.txt)，日志显示 unexpected keyword execution_context；并发测试等待 stream start 无法到达 | 旧 `_prepare_permission_and_tools` fixture lambda 未接受新增内部 context 参数 | 04 测试 | 更新 test_chat_gateway_retry_integration/test_model_gateway fixture 参数接口，保持原模型回退、完成、计费、容量和 lease 断言。未弱化正确预期 | 最终回归含 test_chat_gateway_retry_integration、test_model_gateway、test_model_gateway_concurrency_integration 全部 passed |
| P-04-05 | 末轮边界测试注入撤权、缺旧 permission bool、缓存写异常及 Actor 重入 | 模板准备需前置当前授权；新业务权限应实时读取；缓存失败不能覆盖已完成状态；跨轮重建服务会丢失请求内预占 | 04 | 模板前刷新；只对当前声明/任务要求核验权限；缓存写失败留原结果；同请求服务复用。均已补集成复验 | I::test_scheduled_revocation_precedes_template_copy；I::test_declared_permission_is_loaded_without_trusting_a_prior_boolean；I::test_cache_write_failure_preserves_completed_business_and_single_use；I::test_chat_reuses_request_service_across_model_rounds |

开发过程中发现的符号/fixture 错误均已修正并由最终相关全组覆盖；没有以“历史问题”名义遗留本块失败。唯一继承的分进程测试方式依据是 [01 验收记录](TOOL_UNIFICATION_ACCEPTANCE_01.md#4-问题清单) 的 ERP 收集污染基准复现。

## 5. 用户验证单

确定部署版本：**待用户指令“提交部署”后记录候选 SHA**。以下均未在生产执行；真实写入/删除/付费生成需要用户明确授权及指定的可恢复测试资源。没有该授权时仅做只读与拒绝步骤，批准后的真实写入项保留“未验证”。

| 步骤 | 前提/操作 | 预期 | 用户实际记录 |
|---|---|---|---|
| U-04-01 版本 | 提交部署后记录候选 SHA、测试时间、测试组织/工作区 | 实际生产版本与本次被测源码指纹一致 | 未验证；待候选 SHA |
| U-04-02 普通只读 | 在获准测试工作区检索知识/列文件，观察普通聊天与 Actor 消费 | 正常调用与原结果/图片/文件展示；无多余确认 | 未验证 |
| U-04-03 plan 拒写 | 进入 plan 请求生成/文件删除或业务写入；只观察拒绝 | 明确未执行，原资源无变化，无绕行执行 | 未验证 |
| U-04-04 危险拒绝 | 在指定测试资源发起危险操作，拒绝确认；另观察关闭/等待超时 | 0 业务执行，无重复弹窗，无资源变化 | 未验证 |
| U-04-05 危险批准 | 仅在另行获真实写入授权后，对可恢复测试资源批准一次；核对结果与调用记录 | 1 次业务执行；参数/范围改变不能借用原批准 | 未验证；缺真实写入授权，不执行 |
| U-04-06 定时范围 | 使用获准的只读定时任务；尝试超出确认名单、失效授权或危险动作 | 授权只读正常；其余明确未执行，不扩大定时能力 | 未验证 |
| U-04-07 群/取消/恢复 | 获准群工作区只读访问，取消一次只读任务；有条件时验证 Actor 恢复 | 个人资源隔离、取消停止后续调用、已完成业务不因恢复重做 | 未验证 |
| U-04-08 关闭 | 用户对确定候选完成必要验证后明确“清理工作树” | 受控 accept-and-close 验证生产已测候选与 main tree 一致；不重复部署，之后才可启动 05 | 待用户验收和指令 |

## 6. 结论与交接

**A-04-01～06、G-01～05 技术验收通过，待部署/用户验收。** 本块真实调用入口的本地集成、相关回归及逐项证据齐备；生产用户验证全部待完成，不把 mock Handler 成功表述成真实服务成功。

回退到本块基准 `0f65d72dd00a0fce6885d4df0b7977454f666812`：撤销本任务 04 的 backend 增量并删除 runtime/runtime_context/tool_lifecycle 新模块，保留 01–03 和 `2ed4d783` 文件身份/边界修复。测试/文档随对应代码版本保留记录。没有数据库迁移、新 WS 字段或新的结果持久化载荷；旧 reader 可直接读取本块写出的原格式，无 payload 数据回迁。已有 running/uncertain invocation 仍交原保护机制，不因回退自动重试；跨版本参数哈希/旧批准不匹配明确拒绝。

板块 05 的代码前置已具备；流程仍缺确定候选的提交部署、用户验证、受控验收关闭及 main 包含成果核验。**用户验收关闭前不得进入板块 05。**

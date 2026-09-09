# 工作区附件 ID 解析修复（2026-09-09）

状态：**本地修复及技术验证通过，待提交部署、用户验证**。用户明确要求在工具统一 03 当前工作树修复；不是板块 04 接入。

追加核验：路径归一化边界回归已修正并增加第 22 个用例；后来发现的两个旧文件资源边界缺口也已按用户授权修复，详见 [边界修复与兼容验收](FILE_TOOL_BOUNDARY_REPAIR.md)。当前验证为主组 1481 + 独立 ERP 11 = 1492 passed，4 原有 xfailed；原核验历史见 [其他工具核验](OTHER_TOOL_LOGIC_AUDIT.md)。下文为附件 ID 修复的阶段记录，不作为全产品无缺陷证明。

## 范围与版本

- 工作树：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-03`。
- 分支：`codex/task/20260909195904-tool-unification-03`。
- 修复基准 / 当前 HEAD：已部署候选 `e243ba2c0d545d8afca3ae930a40389276b7c123`。本次被测版本为该提交加未提交修复；不能把基准 SHA 当作包含修复的生产版本。
- 四处生产源码增量：FilePathCache 的身份/索引，file_id 的反查接口，file_analysis_service 的状态写回，attachments 的状态读取；新增身份回归测试，并将原分析测试的两项调用参数断言改为完整源路径。其他工具统一代码未修改。

## 已验证原因与方案

生产只读诊断证实：用户插入的是 Excel 文件，附件具有 CDN URL、workspace_path 和正确 file_id；同一对话此前搜索过该文件。搜索先登记 basename，再登记相对路径，第二次登记因归一化同名去重直接返回。ID 由相对路径生成，而解析器遍历的登记键缺少相对路径，因此失败。路径读取通过文件名兜底成功，最终完成分析。这解释了“先失败、搜索后换路径又成功”，并非 CDN 链接缺失。

该机制还允许不同目录的同名文件共享 FileEntry，从而串用 workspace、Parquet 与 analyzed 状态。原 ID 测试仅覆盖首次直接登记完整路径；原多模态搜索测试只断言按文件名能读取，没有检查搜索输出的 ID。

采用缓存共同机制修复，保留现有调用方：

1. 按相同实际 workspace 路径合并文件；没有 workspace 时只使用精确、唯一的登记键。归一化名字仅用于查找，不作为身份。
2. 每次 register 都补全登记键。完整相对路径与兼容名字分开保存；子目录文件拿到完整路径后，之前的 basename 降为名字别名。根目录真实文件的完整路径优先于子目录的同名别名。
3. 精确名字、归一化、stem、前缀均要求唯一命中，歧义返回 None；resolve_path 延续 FileNotFoundError 行为，不任意选第一个文件。
4. `registered_paths()` 返回可唯一匹配的精确键快照；ID 解析不再读取私有索引，不进行模糊匹配，多个源文件哈希冲突返回 None。`compute_fid`、ID 格式和旧唯一名字兼容出口保持不变。
5. 分析状态以绝对源路径写回，附件以 workspace_path 读取；缓存限额按文件条目计算，淘汰时同时清除全部索引及状态引用。

仅补一条 rel_path 键会留下同名串文件、状态串用和淘汰残留，因此不足；无需修改模型提示、增加失败重试、改数据库或另建文件 ID 协议。

## 验收证据

`I::` 指 [test_file_cache_identity.py](../../backend/tests/test_file_cache_identity.py)。

| 编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| F-01 | 搜索、目录、单文件描述、当前任务清单登记后，附件 ID 首次分析成功 | 4 入口均经过原发现方法、ChatContextMixin 附件登记、原附件渲染、原 ID 解析及 analyze_file；转换 mock 各 1 次，未知 ID 各 0 次 | 通过 | I::test_discovery_then_real_attachment_registration_resolves_id；最终日志 |
| F-02 | 登记顺序、重复登记不丢 ID/Parquet/analyzed | 6 种顺序全部保留映射及状态 | 通过 | I::test_registration_order_preserves_all_ids_and_analysis |
| F-03 | 同名、归一化碰撞、根目录同名、模糊歧义不能串文件 | 双向登记分别解析正确源路径，歧义不选择任意文件；root 精确路径优先 | 通过 | I::test_same_or_normalized_names_are_distinct_files；test_root_file_exact_path_wins_over_subdirectory_basename；test_ambiguous_stem_and_prefix_do_not_choose_first |
| F-04 | 分析结果和提示状态属于正确文件，淘汰不残留 | 同名文件仅目标得到 Parquet/analyzed，另一文件仍 raw；淘汰后各路径/名字/ID 不再可解析 | 通过 | I::test_analysis_and_attachment_status_target_full_path；test_eviction_removes_all_aliases_and_analysis_state |
| F-05 | ID 冲突/未知/错组织拒绝解析，旧类型、图片 URL 和文件读取兼容 | 新冲突用例及既有 ID、FileReadResult 图片、文件、附件、ToolExecutor 测试通过 | 通过 | I::test_hash_collision_does_not_resolve_arbitrary_source；test_file_id_protocol/test_file_tool_mixin/test_file_id_e2e 等最终日志 |
| G-01 | 授权范围明确，没有后续板块混入 | 附件读取 Bug 为用户明确追加范围；四处源码修复，不接入新 Dispatcher | 通过 | 本记录范围；源码检查 |
| G-02 | 成功、失败及必要边界有独立证据 | F-01～05 已逐项执行 | 通过 | 以上各用例 |
| G-03 | 修改前复现，修改后新增与相关既有回归通过 | 同一套新增测试：基准 21 failed，修复 21 passed；扩大回归 1111 passed、0 failed、4 xfailed | 通过 | 基准 / 最终日志 |
| G-04 | 保持对外协议与兼容出口 | file_id 哈希、schema、旧 path、FileEntry.to_dict 三字段、WS/持久化格式不变；状态由正确文件决定。唯一文件的旧读取、图片返回继续通过 | 通过 | 源码检查及旧测试；原分析测试仅将 basename 断言改为完整路径，并由双文件真实缓存测试替代验证隔离 |
| G-05 | 方案、命令、限制、回退及交接完整 | 本记录与 03 交接增量完成 | 通过 | 本文及交接文档 |

### 命令与日志

在当前任务工作树执行：

```bash
bash docs/document/tool-unification-evidence/run-file-cache-regression.sh
```

- [最终回归日志](tool-unification-evidence/03-file-cache-regression.txt)：**1111 passed，0 failed/error/skipped，4 xfailed，2 warnings**。含 22 新场景及工具统一 Registry/Policy/Dispatcher/ToolResult 全组、旧执行器、文件、图片、附件和任务资源隔离。
- [源码范围与指纹](tool-unification-evidence/03-file-cache-checks.txt)：精确核对六个授权 backend 文件、AST、diff 空白、shell 语法和未涉及的协议源码。
- [修复前基准日志](tool-unification-evidence/03-file-cache-baseline.txt)：通过 `git archive e243ba2c0d545d8afca3ae930a40389276b7c123 backend` 建立临时副本，仅复制本次新增测试，执行 `python -m pytest backend/tests/test_file_cache_identity.py backend/tests/test_attachment_routing_baseline.py -o addopts='' -v --tb=short`，**21 failed、24 passed、4 xfailed**。同一套新增用例在旧代码上全部失败。
- 4 项 xfail 属于原 description 图片说明、描述长度和 system prompt 模态文案，旧基准同样存在；未新增、跳过或削弱这些测试，不影响本次身份解析验收。
- Python 3.14.2；无 `.env`/`backend/.env`；数据库和 Redis 指向不可连接的测试端口，JWT 使用测试占位。两项警告为既有 pytest env 配置项和 Pydantic class Config 弃用。

### 问题与复验

| 问题 | 处理与证据 |
|---|---|
| 登记遗漏、同名合并、模糊任意命中、淘汰残留 | 共同注册表修复，21 个前后对照用例通过 |
| 初轮新测试把含提示文字的附件标签文本作为严格 XML 解析，并缺少描述方法宿主属性 | 改用真实 FileToolMixin 宿主及目标标签提取，保留字段断言；最终同一套测试在旧基准复现失败、修复版本通过 |
| 原有 4 项 xfail | 已在修复前基准复现；不属于本次修复，保持原状 |

本次范围内无遗留失败。生产效果尚未验证，不能以 mock 替代真实用户验收。

## 用户验证与回退

新候选部署后，在同一对话先搜索一个子目录内的 Excel，再从工作区插入并要求读取；首次 file_analyze 应直接成功，不再经历“未找到 file_id”。再用两个不同目录的同名表格，分别插入并读取，确认内容和已分析状态各自正确。图片原有预览、URL 与 FileReadResult 通道由既有测试覆盖，生产可另做一次只读回归。

本次没有执行真实删除、付费生成、双执行、数据库写入或生产部署。仍保留当前工作树，未合并 main。回退本次四处源码和相关测试到 `e243ba2c...` 即恢复修复前版本；无数据迁移，但会重新带回此 Bug。缓存为进程内会话缓存，正常部署重启后按发现/附件链路重新登记。

板块 03 原验收证据保留为首次部署前历史记录；本增量优先解释“生产源码完全未变”的旧描述。板块 04 仍需 03 用户验收关闭后才可启动。

# AI 主分支与自动知识：源码对照及实施记录

2026-10-25。用户已授权：深入参考成熟开源源码，结合既有方案，无重大未决问题后直接实施；未授权生产写入、部署或物理删除旧库。
本任务工作树已从ea97ae93快进到最新origin/main `4e813a21`，包含已核验生产的知识派生/快答及项目删除能力；原任务文档保留。

## 源码证据与采用边界

只读取得固定提交，追踪实际函数，不以README或星数替代机制验证。不引入外部框架依赖、不复制受不同许可证约束的实现。

| 项目与固定源码 | 读取结论 | 本项目应用 |
|---|---|---|
| [Dify retrieval_service](https://github.com/langgenius/dify/blob/303adaec49ba80c903ffd79e32f52cde255993c3/api/core/rag/datasource/retrieval_service.py)、[后处理](https://github.com/langgenius/dify/blob/303adaec49ba80c903ffd79e32f52cde255993c3/api/core/rag/data_post_processor/data_post_processor.py) | 多通道检索再处理；融合/重排阈值不同于向量相似度；失败可见 | 保留召回来源与排序，不将语义失败当空结果；不盲目套同一阈值 |
| [Onyx query filters](https://github.com/onyx-dot-app/onyx/blob/616a4b1a9c75cc07e9645069c672f3e8fb029ca8/backend/onyx/document_index/opensearch/search.py)、[读取访问检查](https://github.com/onyx-dot-app/onyx/blob/616a4b1a9c75cc07e9645069c672f3e8fb029ca8/backend/onyx/access/access.py) | 租户、ACL与知识范围分别约束；原文件访问同样检查权限 | 服务器身份/RLS不变，自动任务选根不等于所有可访问根；快照不冻结授权 |
| [LangMem memory tools](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/knowledge/tools.py)、[后台反思](https://github.com/langchain-ai/langmem/blob/48e3c11f5bb527282c7d5339c6a87a0b35abccfc/src/langmem/reflection.py) | 运行时命名空间限定用户，create/update/delete不同契约，后台整理不阻塞主响应 | 普通个人topic树、服务器绑定、受限更新；持久事件重试，不给模型自由选择用户/目标ID |
| [Mem0 add pipeline](https://github.com/mem0ai/mem0/blob/2c81af3bbe27d9bccb09c772d583df9e9be72ee5/mem0/memory/main.py) | 先按用户检索旧记录、映射既有ID再提取；重复过滤与实际持久成功分开；供应商错误不伪装无事实 | 已有条目参与整理、来源引用验证、写成功才记完成；命中次数不提升可靠性 |

Dify当前为带附加条件的许可证，Onyx也区分企业目录；这里只借鉴上述公开机制，用项目既有Python/PostgreSQL能力原创实现，不复制源码。

## 实施任务地图

完成条件：个人库只有一个AI主分支；习惯/方法/工作情况在普通画布可管理；从真实个人任务异步整理、幂等更新、尊重人工修订和删除；任务按需选知识并记录证据缺口；旧知识读写独立退出且指标保留；提供归属明确的离线迁移入口，不对生产自动迁移。

批次与主要文件：

1. `333_knowledge_ai_journal.sql`：个人分支定位、仅存管理元数据的条目绑定、持久处理事件；受控函数复用知识写命令和RLS；正文仍只在kb_objects/versions。真实任务终态为持久事实，后台发现未整理事件，不依赖Web投递成功。
2. `knowledge_workspace/ai_journal*.py`：严格候选协议、来源/敏感信息检查、任务状态与工具事实核对、既有条目更新、租约/重试；复用FairBackgroundJobs/模型客户端；main生命周期接入。
3. `knowledge_workspace/scope_selection.py`和runtime：显式引用保持，默认任务相关个人/团队和唯一明确项目；纯文本任务不全库读取；必要时模型主动重新发现来源；主分支习惯小量上下文，方法/进展按任务召回。
4. answer_plan/检索策略：正文+同章例外与必要规则、partial/语义故障退回可补查路径，不能过早用missing封死工具；预算与来源保持。
5. 旧库独立开关：指标不受旧经验/搜索停用影响；停止seed重建、旧经验/评分节点生产；兼容旧任务名称但不再默认广告；离线迁移输出逐条判定/来源映射，不将全局/组织内容默认为个人。
6. 真实临时PostgreSQL测试：跨用户/组织、并发创建、重复事件/租约恢复、人工修改/删除不复生、快照/撤权；模拟模型与故障；现有知识/聊天/工具策略回归；文档、静态、按影响构建检查。

自动条目不是业务rule，不以模型的“工作成功”自述验证任务。方法默认候选，仅显式用户验证或可核对的验证依据可以升级；个人数据不因组织管理员身份公开。没有新基础设施或需要改变用户已确认定位的证据，进入实施。

## 本地实现结果（2026-10-25）

三部分已经接入任务工作树，尚未部署或执行生产迁移。迁移编号333；使用既有PostgreSQL、受控画布命令、派生和后台调度，不增加检索服务或依赖。开源参考体现的是成熟机制，不代表存在统一的“大厂认证标准”，也不以源码参考代替业务评测。

### 自动检索与证据

- `scope_selection.py`将权限上界与本次任务范围分开：寒暄、自包含文字变换、明确只用所给资料时不自动冻结全库；其他内部任务选个人/团队，以及唯一命名的项目。明确跨项目命名可多选；同名或尚未定位的项目保留澄清状态，不混用资料。显式引用保持既有优先级。
- `knowledge_discover`允许模型在前置跳过后按需发现；入口核对真实任务/输入、actor/org和私人上下文。已有快照不改范围；查询不能覆盖用户明确的“只根据所给资料”边界。发现后的运行绑定回Handler，下一模型轮更新知识工具可见性，Registry/Policy与Skill授权上界继续生效。通道和定时任务没有因此获得私人范围。
- 同轮版本冻结、Actor重放重新鉴权、原文/规则/来源回执继续复用。`query_terms.py`共享中文关键词；短语保留精确身份，平台别名和混合字母数字SKU不因语义召回而放宽。沿用已有混合排序及阈值，没有把相似度当正确率，也没有为当前规模引入新搜索集群。
- `evidence_assessment.py`区分召回、读取完整、问题充分：在符合快答资格且读全原文和适用规则后，用现有配置模型判断必要条件/例外，返回resolved/unresolved/conflict。服务器校验支持handle必须来自本次真实读取，保存评估状态与来源。最多8个必要依据、24k字符评估包、15秒HTTP超时；仅此条件下增加一轮评估，并非每条消息额外分类。模型判断仍可能出错，不能当形式化证明。
- 只有完整且resolved才进入快答；partial、冲突、缺条件、评估故障、未明确项目均保留定向补查工具。无命中不再直接用快答missing关闭工具。语义检索故障明确返回降级状态，不计作连续空搜。待验证方法和历史工作情况不当成已验证业务事实。

### 个人主分支与整理

- 在当前个人容器中懒创建一个普通顶层主题及三个子主题。定位表仅保存ID；普通document/version是唯一正文来源。用户改名按ID继续关联；修改正文后后台不覆盖，移出子树或删除后不自动搬回/复建。初次创建有actor/org互斥和唯一约束。
- 后台从部署时间之后、归属一致的真实个人任务终态发现事件，不依赖WebSocket通知。队列只有管理状态、来源ID/位置摘要和哈希，不保存另一份知识正文或完整对话；模型不选择目标用户、容器或任意对象ID。
- 每事件最多3条、每正文最多6k字符、最多5次尝试、4分钟租约、两项并发。来源再鉴权、任务状态和租约在写前重验；全部条目及完成状态同事务提交，崩溃不能留下“半写完但未完成”的重放窗口。用户主动删除会保留绑定，旧事件不能复生。事件结果记录saved/unchanged/duplicate/user_edited/user_removed等状态。
- 使用习惯必须来自用户明确长期要求，保存原话而非模型改写；一次性要求不变成习惯。方法优先采用Actor持久化tool_invocations中的完整成功结果，安全/旧调用才回退异步审计；invocation的succeeded仅表示返回，必须另验业务result.status，uncertain/error不能被审计success覆盖。方法须有真实成功工具记录，默认candidate；只有用户原文明确验证才可verified。工作记录保留任务实际终态，AI整理摘要标待核对，不把工具层success等同业务成功。
- 整理前按当前问题检索既有条目；模型已有正文总预算12k字符，完整条目放不下就标protected，不截断后覆盖丢失适用条件；稳定主题键复用对象，重复事实即使换会话链接或模型键也不新增。人工修订不自动合并覆盖；不从派生触发再整理，避免自我反馈。
- 习惯每轮重新读取，最多12条/2400字符；方法/工作情况走按需检索。建立可管理分支后，不再注入缺少逐事实删除谱系的旧`user_facts/user_memory`段，保留用户明确Custom Instructions；没有建立分支的用户继续原记忆兼容。Mem0存储与其他功能没有删除或做双向同步。关闭自动整理仍读取已有习惯，删除不会因关worker而从旧记忆回来。

### 旧库退役与迁移

- 默认关闭旧search、extractor、ExperienceRecorder、seed重建、评分节点聚合；服务入口与生产者均有独立开关，旧工具不默认广告，历史调用收到明确停用结果。旧指标写入有独立`kb_metrics_enabled`，不依赖旧知识开关；历史指标/评分/图数据保留，未DROP。
- `scripts/migrate_knowledge_journal.py`默认只读生成逐条决策/指纹报告，不加载deploy配置；不在报告输出正文。只有匹配actor、org、`user:<actor>`且属于experience的内容能成为个人候选方法；全局/组织/模型参数进入归档或归属审阅，不复制给每个用户。
- `--apply-reviewed`要求完全一致的先前报告；函数写前再验来源指纹。来源变化整批回滚，旧ID映射幂等，用户后续修改受保护。导入仍是待验证方法，不继承旧confidence/hit_count。生产655条尚未逐条迁移，不能声称已迁完或已删除旧库。

## 开关、运行与交付边界

| 配置 | 含义 |
|---|---|
| `KNOWLEDGE_WORKSPACE_ENABLED`、用户/组织白名单 | 继续是所有新知识能力的开放边界 |
| `KNOWLEDGE_DERIVATION_ENABLED` + `KNOWLEDGE_DEFAULT_SCOPE_ENABLED` | 自动范围/模型发现；显式引用保持兼容 |
| `KNOWLEDGE_EVIDENCE_ASSESSMENT_ENABLED` | 默认启用充分性评估；关闭时保留导航，不能恢复未评估直接快答 |
| `KNOWLEDGE_AI_JOURNAL_ENABLED` | 停止/恢复后台新整理；不删除正文、不停止已有习惯读取 |
| `KB_LEGACY_SEARCH_ENABLED` / `KB_LEGACY_EXTRACTION_ENABLED` / `KB_LEGACY_SCORING_ENABLED` / `KB_LEGACY_SEED_ENABLED` | 默认false，仅供有明确原因的兼容回退，不能整体重新打开所有生产者 |
| `KB_METRICS_ENABLED` | 独立保留运行指标，默认true |

部署按项目`deploy/release.sh`流程，需要用户对应发布授权；本轮未执行。新增表/函数均前向添加；代码回滚保留知识和元数据。不要删除幂等/已删除条目绑定来“重试”，否则会丢失人工修改/删除的保护。blocked事件需先核对error_code、来源授权和实际任务，再由受控维护处理，不无限自动重试。

离线迁移示意（在已获授权的目标环境，由操作者提供连接，不在命令行放密码）：

```bash
python backend/scripts/migrate_knowledge_journal.py --actor USER_UUID --org ORG_UUID --report /private/review.json
python backend/scripts/migrate_knowledge_journal.py --actor USER_UUID --org ORG_UUID --apply-reviewed /private/review.json --report /private/result.json
```

无组织用户省略`--org`。报告路径须全新，权限0600；apply与review输出不能同名。先核对真实数据库身份和报告归属，再执行生产apply；本轮只在隔离合成库验证函数。

## 验证与限制

- 隔离临时PostgreSQL，以真实runtime/owner权限执行迁移302–333相关链和新函数；涵盖跨用户/组织、单例并发、租约恢复、任务状态变化、批量原子性、幂等、人工编辑/删除、来源指纹、逐轮习惯及旧库停用保留指标。
- 本地知识、聊天/Actor、工具目录/Policy、旧接口显式兼容、模型评分兼容、Skill和通道隐私定向回归。兼容测试明确打开legacy开关；另有测试核对生产默认关闭，不用放开生产默认值来维持旧测试。
- 真实配置模型仅接收合成资料：习惯/方法/一次性要求/失败工作记录4类，充分/不足/冲突/错品牌4类，另测自动选项目→原文→充分性→最终答案与有效来源。没有发送生产私有正文或使用生产数据库。
- 这些证明链路和所测边界，不是95%召回、98%引用等统计承诺；尚未完成200条业务金标与线上WebSocket/生产负载灰度验收。RRF/付费重排及阈值调整仍须靠对照收益决定，不为“大厂同款”盲目增加基础设施。发布后必须用获准业务样例观察质量、延迟、费用和错误，再验收。

### 本轮验证记录

2026-10-25本地21个定向测试模块合计1191通过（15.06秒）；追加完整条目预算边界后，journal模块20项通过（包含CLI只读审阅、apply和重放实跑）。真实配置模型的三组opt-in测试分别通过：四类沉淀案例43.02秒、四类充分性案例6.08秒、自动命名项目→原文→评估→最终来源答案8.16秒。以上是单次合成验证耗时，不能据此宣称生产p95或大样本正确率。Python编译和`git diff --check`通过。没有执行生产迁移、发布、旧表删除或工作树关闭。

发布前补充核验发现Actor持久调用与异步审计并存，已把持久记录作为优先事实源，并新增旧/新结果协议、业务错误、uncertain优先级测试。首次候选061bd4ee在tests_and_build阶段主动停止，执行器返回failed_safe，未同步生产文件、未执行333；修正后重新提交正常完整发布。完整测试还发现模块缓存导致旧开关测试取到不同Settings实例，新开关改为从get_settings读取；兼容测试显式打开旧功能，不改变默认退役状态。

完整发布检查两轮分别12910项断言通过，但macOS/Python3.12在Py_FinalizeEx清理生成器时崩溃，原生栈进入_time_machine_time；发布入口均返回failed_safe，未继续生产同步或迁移。提前gc.collect未解决，已撤销。现有时间冻结依赖从2.14.1固定更新至2.19.0（保持2.x API）；上游2.18起在最后一次travel结束后还原C函数补丁，并补齐NULL错误处理，参见[更新记录](https://time-machine.readthedocs.io/en/latest/changelog.html)及[issue532](https://github.com/adamchainz/time-machine/issues/532)。最终仍要求统一发布入口全量检查正常退出，不忽略退出码、不跳过测试。

随后80d9a8fe全量测试正常通过并进入生产同步，333迁移因真实tasks没有updated_at而事务回滚（kb_ai_events不存在、333账本记录为0），服务未重启且健康。已修正夹具以使用真实created_at/completed_at字段，扫描按真实时间兜底，并在任务状态事务内通过触发器入队，覆盖老任务首次暂停而没有completed_at的情况；22项真实PostgreSQL定向验证通过，包含暂停重复幂等和群聊隔离。只有核实原锁、无在途写入、迁移和服务状态后才走受控恢复，再正常提交发布。

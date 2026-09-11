# 05 附件读取生产复验与当前消息绑定

2026-09-11。生产版本为 `1df0e01c472e3b55b6e13d0e906199a78920ed5c`。本次候选尚未提交部署，用户验收未关闭；**05 不能标记技术通过，不能进入 06**。此前部署成功只证明发布及自动检查通过，不能替代附件读取验收。

## 生产事实与结论边界

只读核对 09:52:30 的工作区插入与 09:53:35 的重新上传：

| 环节 | 两次请求的证据 |
|---|---|
| 前端提交、消息存储 | user blocks 均有文件名、URL、workspace_path |
| 工作区落盘 | 按任务所属用户/组织解析的两个文件均存在，分别为 27,548,952 和 6,702 字节；未读取业务表格内容 |
| Actor 输入和模型上下文 | 输入锚点对应本次 user；checkpoint 的附件 XML 中均有正确当前文件名、路径、fid 和 read_call；实际适配器为 DashScopeChatAdapter / qwen3.5-plus |
| 本次 user 的模型投影 | 只有“读取文件”；附件身份在独立 system 消息中 |
| 实际回复 | 首次没有调用读取工具而声称无附件；第二次搜索上一份文件名称，未读取新上传的文件 |

因此，上传、落盘和 Actor 透传并未丢失文件。本次生产表现是模型没有把独立附件信息正确关联到最新请求。无法从这两次生产轨迹证明 `1df0e01c` 的历史结果恢复是唯一诱因；修改前后合成对照也不支持宣称该提交普遍破坏了文件输入。前次验收只验证字段到达 HTTP 请求体，没有验证真实模型能识别本轮附件，这是验证缺口。

原始生产记录只在受限临时文件保存：`/private/tmp/tool05-attachment-production-state.json`、`/private/tmp/tool05-attachment-paths.log`，权限 0600，不进 Git。仓库中的[观察记录](tool-unification-evidence/05-attachment-binding-observations.json)只保留生产事实摘要和合成模型实验，不含真实聊天和表格内容。

## 对照实验与被否定的方向

[百炼兼容文档](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)的 messages 参数说明仅首条支持 system；[Qwen 官方模板](https://huggingface.co/Qwen/Qwen3.5-9B/blob/0fd2a3858f6cee5f5b72ef2e21276bcf9a45ff55/chat_template.jinja)也检查 system 位置。但不能由此推断生产接口已忽略全部后续 system。以下实测结果优先于这个假设：

| 合成场景 | 887b28ae 的提示/历史投影 | 1df0e01c 的提示/历史投影 | 只把系统消息合并到开头 |
|---|---|---|---|
| 工作区附件 | 正确 file_analyze(fid) | 正确 file_analyze(fid) | 误报无新附件 |
| 重新上传、历史有“无附件”回复 | 正确 file_analyze(fid) | 正确 file_analyze(fid) | 误认上一份文件、没有读取调用 |

**没有采用合并系统消息的改动。** 实验说明文件信息的位置与本轮归属会影响识别，不能把所有问题归结为“多条 system 被丢弃”。每个条件仅调用一次，不是确定性或统计显著性证明；合成历史不是生产原始聊天回放。

用户分别授权了实验范围及次数。最初包含内部提示的请求被自动审批拒绝，未执行；随后改用完全合成的提示和工具做简化实验，只收到 1 条成功结果，连接失联后其余结果无法确认，不计为成功。核验服务器没有该实验进程后，只终止经父进程脚本和命令核验的本机 SSH 连接，未操作生产服务。用户随后明确批准内部提示/工具定义与新增 6 次完整对照，完整结果均已记录。

## 候选修复

修复用户输入投影中的附件归属表达：原文保留为第一个 text part，第二个 text part 仅列本条消息附件的 `file_id/name/path`。这些标识与当前消息处于同一 user 消息，独立 XML 的状态/action 规则仍在原 system 位置。没有通过识别“读取”关键词、强制调用某工具、替模型选择历史文件或吞错误实现修复。

- `chat_context/attachments.py` 新增 `format_current_attachment_refs`：只投影当前 workspace_files，使用原 compute_fid，不搜索历史、不读取文件、不授权新路径。
- `prompt_builder/layers/user_layer.py` 使用已有 workspace_files 生成当前附件引用；用户原话逐字保留。无当前附件时仍为原字符串；图片只保留原有一次 image_url；legacy inline XML 模式不重复追加引用。
- `prompt_builder/builder.py` 把现有 org_id 传给用户层，保证 user 引用和附件 XML 的 fid 一致。

| 边界 | 适配关系与兼容性 |
|---|---|
| 模型输入 | 同一 user 内容中的原文 text part + 当前文件引用 text part；工具描述、系统规则顺序、完整附件 XML 不改 |
| 模型结果 | Chat to_message_content / ToolLoop to_tool_content 不改；错误、timeout、empty、partial、plan、uncertain 和取消仍沿原消费链 |
| 前端 | 无源码改动；原 user 文件卡片、图片引用、WS block、delivery、emit/sink 继续原协议；模型的第二个 text part 不写回用户原始消息 |
| 审计 | 无新增审计写入或模型业务任务；实验只请求模型选工具，没有执行任何返回的工具调用 |
| 持久化 | 继续原 messages blocks 和旧 checkpoint JSON；新增 user text part 可由旧格式无损保存/恢复；已冻结 checkpoint 不重算或改写；无 ToolResult 对象写入 serializer |

回退候选只需撤销上述 3 个生产文件的差异。无新迁移，无前端变动，无数据清理，不回退已部署的 253 删除修复或前次结果状态恢复。

## 修复后验证

用户另外批准最多 6 次修复后调用，实际完成 6 次。使用真实 UserLayer 输出、同一百炼配置、`qwen3.5-plus`、temperature 0.1、关闭 thinking、输出上限 384 tokens；每次 40 秒、整个进程 280 秒上限，开始/结束及结果同步写本机和服务器临时记录。密钥不离开服务器。

| 条件 | 工作区 | 重新上传 |
|---|---|---|
| 当前原生系统消息顺序 + 当前附件绑定 | 正确 fid，file_analyze | 正确 fid，file_analyze |
| 前面已复现失败的 system 前置位置 + 当前附件绑定（仅实验） | 正确 fid，file_analyze | 正确 fid，file_analyze |
| 追加合成旧统计历史，约 1.4 万输入 tokens + 当前附件绑定 | 正确 fid，file_analyze | 正确 fid，file_analyze |

6/6 都精确使用当前 fid，没有搜索上一份文件。没有执行 file_analyze 或其他工具，不能把它称为真实工作簿分析通过。完整前置对照消耗 57,971 tokens，候选对照 67,173 tokens；不把结果不明的首次简化实验计入此数。

[新增 13 项测试](../../backend/tests/test_current_attachment_binding.py)中，11 项附件绑定断言在未修改代码上失败、2 项原兼容行为通过。这是输入关联约束回归，**不是**原生产模型错误的离线复现。修复后 13 项全部通过，包含工作区/上传、xlsx/csv/pdf/png、字符原样保留、混合附件和图片不重、无附件不借用历史、legacy inline，以及真实 PromptBuilder → DashScope HTTP body → 原 checkpoint 链路。

使用 `bash docs/document/tool-unification-evidence/run-05-attachment-binding.sh` 可复验同组检查。最终受影响回归 **718 passed、3 skipped、0 failed/error/xfail**，见[完整结果](tool-unification-evidence/05-attachment-binding-regression.txt)。3 项均为原 V1 gather 测试跳过，未改跳过条件。企微测试退出时有 StreamKeepAlive 未清理协程提示；在干净导出的已部署 HEAD 上，同组原测试 **705 passed / 同样 3 skipped** 且复现同一提示，见[基线](tool-unification-evidence/05-attachment-baseline-regression.txt)，不是本次新增回归。本任务不顺带修改企微保活机制。

合成请求可用 [fixture 构造器](tool-unification-evidence/build-05-attachment-fixtures.py)离线重建，已逐字段验证与实际发送的两组输入完全相等；构造器不调用模型。输入指纹、生产文件源码指纹、全部实际模型响应与 tokens 保存在观察记录中。原 18 份结果协议 goldens 未修改。

## 05 增量验收记录

| 编号 | 本次证据 | 状态 |
|---|---|---|
| A-05-01 | 当前 user 原文/引用同轮；Chat/Loop 原结果投影相关集成仍通过 | 自动化通过 |
| A-05-02 | 无前端/WS/emit/sink 改动，相关原消费协议用例通过；未做新的人工展示验收 | 自动化通过，人工待验 |
| A-05-03 | 新关联测试、13 项绑定用例、6 次真实模型正确选当前文件；原用户会话在新候选上未复验 | **未通过，待生产复验** |
| A-05-04 | 原结果状态与取消/uncertain 相关回归通过，未改状态机制 | 自动化通过 |
| A-05-05 | 新 text part 经实际 HTTP 和旧 checkpoint 无损对照；没有新增审计/业务投递 | 自动化通过 |
| G-01 | 附件读取验收缺陷，3 个生产文件局部修改，无 06 内容 | 通过 |
| G-02 | 必需的原生产场景和人工验收未闭合 | **未通过** |
| G-03 | 718 通过；3 原有跳过及企微保活提示有干净 HEAD 对照 | 通过 |
| G-04 | API/工具 schema/ToolResult 两种结果投影/WS/原持久化不改 | 通过 |
| G-05 | 版本、对照、候选范围、限制与回退已交接 | 通过 |

生产仍是 `1df0e01c`，本候选未部署。待用户“提交部署”后，在同一旧会话分别插入工作区文件和重新上传文件，再发送新消息“读取文件”；核对实际读取的是当前文件、文件和图片能打开、没有自行执行旧统计任务。旧冻结 checkpoint 的继续执行不代表新消息投影已生效。用户验收关闭之前保留本工作树，不进入下一块。

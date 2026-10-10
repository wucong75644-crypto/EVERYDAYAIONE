# 百炼内置工具接入与千问 3.8 升级

日期：2026-10-10  
状态：已按用户授权实施并完成定向验证；后端内置工具、缓存默认开启；未提交、推送或部署。  
任务分支：`codex/task/20261010102444-bailian-builtin-tools`  
调查基座：`6d9b235c0652e82b66a65b522a9c63730ba9b0bd`

## 1. 已确认范围与边界

- 扩展已有 `DashScopeChatAdapter`，主模型直接使用百炼内置联网搜索和网页抓取。
- 不新增 Provider、搜索中转 Provider、本地搜索或抓取工具、第二套网关或事件通道。
- 千问 `qwen3.5-plus` 升级为 `qwen3.8-max`，`qwen3.5-flash` 升级为 `qwen3.8-flash`。同步前后端有效配置及相关调用。
- DeepSeek 型号、默认选择及现有行为保持不变。当前 `agent_loop_model` 默认值为 `deepseek-v4-pro`。
- 不迁移、重写或清理 ERP、订单、商品、知识库和历史会话等业务数据，不新建业务表。
- 历史记录保留原始型号；旧配置如需兼容，在读取/调用边界映射，不批量改写数据库。
- `qwen-turbo`、`qwen-vl-max` 等非 3.5 系列不在升级范围。发现其他 3.5 型号时先核验合法对应型号与能力，不能机械替换。
- 首期仅搜索与抓取，不接入代码解释器、搜图、PDF 理解，不自动升级 Kimi。
- 不擅自改变用户积分规则，不自动发布。

内置工具不会获得直接访问平台数据库的权限。提交的模型上下文仍会发送给百炼，模型形成的搜索查询词会进入联网检索链路；工具启用不代表数据完全不出平台。网页资料按不可信外部内容处理，不得改变现有业务权限。

## 2. 当前实现与复用点

| 位置 | 当前职责与缺口 |
| --- | --- |
| `backend/services/adapters/dashscope/chat_adapter.py` | 已有鉴权、客户端、流式/非流式调用、错误处理和连接关闭；目前仅实现 Chat Completions，未解析 Responses 内置工具 |
| `backend/services/adapters/factory.py` | 模型注册、适配器工厂、企业密钥优先解析；继续复用 |
| `backend/services/adapters/types.py` | 已有 ModelConfig、StreamChunk、ChatResponse；补充必要可选字段 |
| `backend/services/model_gateway.py` | 统一流调用、并发、超时、取消等控制；保持统一入口 |
| `backend/services/agent/tool_loop_executor.py` | 业务工具循环；需识别内置工具执行和 Responses 函数往返 |
| `backend/services/agent/web_search/service.py` | 现有搜索工具的 Provider 选择；不在这里新增百炼中转 Provider |
| `backend/services/agent/web_search_engine.py` | Gemini 搜索及千问 enable_search 兜底；原路径按能力保留 |

当前有效配置以千问 3.5 为主，代码调查未发现 3.7；不将用户口述版本当作替换依据。

## 3. 调用链与执行权

```text
现有 Agent → ModelGateway → DashScopeChatAdapter → 百炼 API
                                            ├─ 内置搜索/抓取：百炼执行
                                            ├─ 业务函数指令：我们执行并回传
                                            └─ 正文/来源/状态/用量：现有输出链路
```

能力和本次请求均允许内置工具时采用 `/compatible-mode/v1/responses`；其他调用继续兼容 Chat Completions。内部请求转换及事件解析复杂时可拆辅助模块，但继续共用适配器入口、鉴权、HTTP 客户端、错误类型和资源生命周期。

Responses 接入必须覆盖消息历史、图片等现有输入、函数定义、函数输出与 call_id、文本与思考增量、完成/失败状态和用量。不能只更换 URL 或追加 tools。

内置工具事件不得进入本地 ToolExecutor。模型已经完成搜索时，避免因“未调用本地工具”强迫重复调用；ERP 问题仍必须使用 ERP 数据，搜索不满足业务数据查询约束。内置工具与业务函数可混合使用，下一轮保留必要协议标识和上下文。

## 4. 能力与启用策略

- 在现有模型配置中区分 Responses、内置搜索及抓取能力；不能把已有 `supports_search` 直接等同于百炼内置搜索。
- 能力按具体型号、地域和工具组合验证。禁止按供应商品牌无条件开启。
- 本次请求还需允许联网；摘要、记忆提取、知识提取等后台调用不因升级自动挂载联网工具。
- 声明 `web_extractor` 时必须同时声明 `web_search`。真实账号已确认非思考模式会拒绝 `web_extractor`；普通模式仅挂载搜索，`enabled` / `deep_think` 或非 `none` reasoning effort 时再挂载抓取。
- 对启用内置搜索的请求移除旧自定义 web_search，避免重复路径；不支持内置能力的模型保留原路径。
- 继续由平台管理历史及压缩，首期 `store: false`，不引入服务端会话链。
- 根据 Responses 的上下文预留约束调整预算，不依赖上游自动截断来保持历史完整。

| 型号 | 搜索 | 抓取 | 首期处理 |
| --- | --- | --- | --- |
| qwen3.8-max | 官方列明支持 | 官方列明支持，需思考模式 | 已启用；实际账号基础 Responses 调用已验证 |
| qwen3.8-flash | 官方列明支持 | 官方列明支持，需思考模式 | 已启用；搜索、抓取、混合函数、图片输入及缓存均已实测 |
| kimi-k3 | 官方列明支持 Responses 搜索 | 抓取文档未明确列出 | 协议可复用；不在本次自动新增/升级型号，不启用未确认抓取 |
| 当前 kimi-k2.5 | 搜索文档未明确列出 | 未确认 | 保留现状 |
| DeepSeek | 部分型号在文档中支持 | 需逐型号核验 | 本次型号、默认及行为保持现状 |

未明确列出表示尚无足够证据，不等于已证明不支持。Kimi K3 仅保留已确认搜索的协议能力映射，未加入平台可选模型、未做账号实测，不能据此宣称当前 Kimi K2.5 已支持内置搜索。

## 5. 千问升级

完整扫描有效代码、配置、模型目录、前端选项、测试及已保存配置的读取路径。重点包括：

- `backend/core/config.py`：路由、记忆、摘要、知识提取及图片提示词增强等。
- `backend/config/smart_models.json`：智能模式型号与默认千问选项。
- `backend/services/adapters/factory.py`：注册、能力、上下文及价格配置。
- `backend/services/adapters/dashscope/chat_adapter.py`：现有本地价格及协议参数。
- `frontend/src/constants/chatModels.ts` 及实际模型选择/订阅白名单：名称与可选项一致。

同步核验新模型思考参数、输出限制、超时、上下文和价格。有效配置替换，历史说明和证据不全局替换；旧持久化配置在读取边界兼容，避免未知旧型号触发工厂默认模型兜底而改变含义。

## 6. 输出、失败与计量

在统一输出类型增加可选内置工具事件、来源和用量，沿现有网关和前端通道传递。前端展示搜索/读取状态及实际来源链接，不另建通道。

来源列表与句子级引用映射分别记录；只有链接时不能生成未经验证的引用位置。区分未执行搜索、无结果、工具失败及响应中断，不能用模型正文自称已搜索代替执行证据。

记录 Token、搜索次数、抓取次数及实际执行状态，复用现有记录机制；具体持久化与结算位置实施前沿调用链定位。工具免费不代表模型 Token 免费。用户积分规则未决定，本次不自动新增扣费规则。

复用现有总时间预算、并发、取消及重试机制。确保网关不会因收到非正文工具事件误判空响应/空闲超时。发生中断或重试时不得重复执行已成功的业务写操作；已有部分输出和无法确认的上游状态必须如实处理。

## 7. 实施批次与验证

1. 定位实际型号引用和读取边界，核对官方参数、价格、账号权限；验证 3.8 普通聊天、思考、图片与函数调用。
2. 扩展现有百炼适配器：输入转换、Responses 流解析、函数调用及结果往返；复用现有测试结构做定向测试。
3. 接入 Agent 能力开关和工具装配，验证搜索、指定链接读取及与业务工具混用。
4. 补齐现有流式展示、来源与用量记录，同步文档和有效模型选项。
5. 验证历史配置兼容、后台提取任务、取消/超时/中断重试及 DeepSeek 回归。

验收场景：普通聊天无意外联网；需要最新资料时出现真实搜索事件；链接读取出现抓取事件及实际来源；ERP 查询仍由现有工具执行；混合工具调用正常完成；旧型号配置不误落到默认模型；内置工具不在本地重复执行；失败不伪装成功；业务数据和权限保持原状。

按变更风险运行适配器、工厂、网关、工具循环及前端相关定向检查，不默认全量测试。真实账号调用产生的费用和验证结果记录在任务证据中，不输出密钥。

## 8. 回滚与待验证项

内置工具采用功能开关，可关闭并恢复旧协议和旧搜索装配。模型升级保留可回滚配置与旧型号兼容策略，不修改历史业务数据，不需要业务数据库回滚。

已验证开发账号的 Qwen3.8 Flash 搜索/抓取与业务函数混用、真实流事件/来源/用量结构、图片输入、Session 缓存及 Max 基础调用。生产业务空间和企业自有 Key 的权限/地域仍需上线测试时确认。文档支持不能代替真实验证。出现影响已确认范围的差异时先报告证据，不擅自扩大到新基础设施或数据库迁移。

## 9. 官方依据

- [Responses API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)
- [联网搜索与型号支持](https://help.aliyun.com/zh/model-studio/web-search)
- [网页抓取与型号支持](https://help.aliyun.com/zh/model-studio/web-extractor)
- [Qwen3.8 Max](https://help.aliyun.com/zh/model-studio/qwen3-8-max)
- [接入地址](https://help.aliyun.com/zh/model-studio/base-url)

以上能力依据 2026-10-10 的调查；实施时复核官方变更及账号能力。


## 10. 默认缓存与 SaaS 隔离

用户追加要求：缓存必须接入且后端默认开启。本次沿用现有 PromptBuilder、网关日志和 Langfuse，没有新增缓存服务或业务表。

- `DASHSCOPE_BUILTIN_TOOLS_ENABLED=true`：支持型号且本轮权限允许联网的 Agent 请求使用 Responses；摘要、记忆等辅助调用继续走 Chat Completions。
- `DASHSCOPE_SESSION_CACHE_ENABLED=true`：Responses 发送 `x-dashscope-session-cache: enable`；Chat 为千问保留已有标记，并在最新 user 消息添加历史缓存端点，无标记时补充首条 system 缓存端点，最多四个标记。关闭只撤销本次增加的显式/Session 缓存，模型自带隐式缓存行为由百炼控制。
- 继续 `store: false` + 平台管理完整历史；不创建/保存/共享 `previous_response_id`，不复用跨用户会话指针。适配器只转换本请求上下文，不将用户结果保存到共享应用缓存。
- 工具名及 schema key 按稳定顺序发送，避免无意义顺序变化破坏缓存。原始消息不被适配器修改；DeepSeek 保持原型号、协议选择及缓存参数行为。
- Qwen3.5+ 合并多条 system 消息；拆分 system 不能保证独立缓存端点，因此不改公共 PromptBuilder 的角色或顺序。动态 system 内容、工具集合变化、前缀不足 1024 Token 或缓存过期均可能影响命中；开启不代表每次一定命中。
- 读取 Chat `prompt_tokens_details`、Responses `input_tokens_details` 的 `cached_tokens`；写入量兼容顶层、details 和 Responses 实测 `x_details[].prompt_tokens_details.cache_creation_input_tokens`。聚合进入现有请求 usage、采样日志及 Langfuse，同时记录内置搜索/抓取次数。
- 沿用平台 Token 计量/积分算法，不引入工具调用额外扣费或缓存折扣结算规则；仅随新型号更新现有模型价格配置。
- 同轮函数调用保留必要 reasoning/search/extractor 回放项；Actor 自有 call ID 用于函数往返。压缩归档时移除被归档消息的隐藏 Provider 状态，避免隐性超预算。

## 11. 真实账号验证证据（2026-10-10）

使用当前开发配置，仅发送公开测试材料；混合业务函数用内存模拟 lookup，没有读写业务数据库。

| 场景 | 结果 |
| --- | --- |
| Flash Chat 显式缓存，重复固定长前缀 | 首次写入 1397 Token；第二次命中 1397 / 1413 输入 Token |
| Flash Responses，Session header + store false + search | 首次写入 1719 Token；第二次命中 1719 / 1727 输入 Token |
| Flash 真实搜索 | 收到 web_search_call、官方来源链接；usage.x_tools.web_search.count=1；无本地调用 |
| Flash 真实网页抓取 + 函数混用 | 抓取官方页面后调用 lookup；Actor call ID 模拟重映射后成功回传并回答；第二轮命中 560 Token |
| Flash 非思考 + 抓取 | 上游返回 InvalidParameter，要求开启思考；已按思考模式裁剪工具声明 |
| Flash 图片 Responses 输入 | 64×64 红色测试图识别正确 |
| Max Responses 基础输入 | 正常完成短文本回复 |

验证覆盖流式增量/最终帧、函数参数去重、不完整流拒绝执行、来源 URL 校验、缓存字段解析、跨模型状态隔离、权限与辅助离线路径、旧订阅幂等/取消、附件和历史回放、上下文压缩、网关重试/并发及前端模型选择。跳过的依赖真实基础设施集成测试不冒充已执行。生产发布及页面端到端验收尚未执行。

补充官方依据：
- [Responses Session 缓存](https://help.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api)
- [显式缓存限制与最佳实践](https://help.aliyun.com/zh/model-studio/explicit-cache-guide)
- [上下文缓存](https://help.aliyun.com/zh/model-studio/context-cache)

最终定向验证：后端 734 项通过、11 项依赖环境的集成测试跳过；前端模型/选择/订阅 75 项通过；TypeScript 构建类型检查与 git diff --check 通过。没有执行生产发布或业务数据迁移。

## 12. 发布前合并与复验（2026-10-11）

同步 main 后保留新增 Kimi 请求规范、共享多模态转换和结构化错误处理。发布前全量后端验证首次为 11794 通过、280 跳过、4 xfailed、6 失败；其中四项旧测试桩暴露了新可选事件字段缺少类型边界，一项不可变配置基线需明确容纳已授权千问升级，一项为首次字体缓存初始化导致内核启动超时。已补充字典/布尔类型边界及精确升级夹具，预热测试环境字体缓存后，在发布使用的 Python 3.12 下对失败模块及相关网关、适配器、执行内核复验：534 通过、3 跳过。重发复用其余已通过的全量结果，仍执行构建、数据库身份核验、部署及健康检查；首次失败发生在生产写入前。

# 强制工具参数 grammar 编译超时：源码与请求因果核验（2026-11-25）

状态：仅诊断；本轮没有修改业务代码、提交或部署。10个上一轮业务/迁移文件 SHA-256 与留存指纹完全相同。此前“供应商返回了错误”是事实，但不足以把根因直接定为供应商独立故障。本轮通过实际请求对照缩小原因范围。

## 已证实结论

1. 错误发生在百炼 DeepSeek 模型服务处理强制工具调用的 grammar 校验/编译环节，尚未产出工具参数，ERP 查询没有执行。当前证据支持这是该调用路径的间歇性处理异常。
2. **“我们的参数字段过多/定义太大导致”并没有被证实，字段数量也不是复现所必需的条件。**同一异常分别在5002字节完整定义、2504字节9字段定义、335字节单字符串定义中复现。
3. 同一变体内的请求体规范化SHA-256完全一致，既有成功又有同类失败。不是客户端随机改了schema、重试时遗漏字段或前端追加素材造成。
4. 当前定义确有3个未限定type的筛选value，以及5个允许额外属性的object，属于可以收敛的契约质量问题。但不能仅凭这些特征认定它们导致了本次超时，也不能保证补type/关闭额外属性后永不再现。
5. 内部到底是编译器实现、资源调度、缓存/节点差异，还是某种非确定性grammar生成问题，客户端源码和返回值无法进一步区分。需要供应商结合下列原生request_id查内部trace。**本次没有获得该层日志，因此不宣称底层根因已完全查明或问题已根治。**

## 源码到实际出站请求

`ERPAgent._llm_extract` → `ModelGatewaySession.stream_chat` → `DashScopeChatAdapter._request_body` → `httpx.AsyncClient.stream(POST /chat/completions)`。

- ERP只提供一个工具submit_query_plan，tool_choice明确指定该工具。
- 适配器在此路径直接传递工具定义和tool_choice，没有自行添加strict、json_schema或新的条件/正则。
- 实际出站请求字段只有model/messages/stream/enable_thinking/stream_options/tools/tool_choice。
- model=deepseek-v4-pro，enable_thinking=false；strict未设置，response_format未设置。不是关闭思考失效或混用两种结构化输出协议。
- 仅1个工具，定义规范化大小5002字节，messages共21683字节（固定失败用户原话和冻结的会话时间）。工具哈希与上一轮失败日志相同：`18867f4529c0615693983707764bb008657c6d1045494bba735edce4aface72a`。
- 工具41个声明属性（跨各对象累计，不是41个顶层参数），7个object，其中5个没有关闭额外属性。3处value仅有description，没有type。没有本地显式$ref递归。完整JSON树深度12包含properties/items等schema关键词层，不等于12层业务对象嵌套。
- 本地既有AJV的标准JSON Schema校验通过；**这不能替代百炼/DeepSeek实现兼容性验证**。
- 历史失败只留存了schema、大小/哈希及错误字符串，并非原始完整HTTP抓包；本轮从未变化的源码和固定输入重建，经原生适配器生成后，在HTTP发送钩子记录实际request.content解析结果，不记录Authorization。当前实际请求与历史schema哈希、模型、输入字节数一致。

## 实验方法

真实相同供应商与模型、相同用户原话/时间/提示词；仅变更实验指定的schema部分或tool_choice。没有数据库写入，没有生产任务或图片生成。交错轮转各变体顺序，避免把单一时间窗口全部分配给某一个schema。

第一阶段16次：当前定义、只限定value类型、只关闭object额外属性、精简到9字段，分别4次。
第二阶段24次：当前定义、9字段、当前定义改auto、单字符串工具，分别6次。保留同一变体中成功和失败的规范化请求体哈希。
第三阶段12次：单字符串开放对象、单字符串关闭额外属性、单字符串显式strict=false、单字符串关闭额外属性且auto，分别3次；每次创建/关闭HTTP客户端，核对生产适配器的会话生命周期，避免仅依赖共享连接结果。

这些是**编译路径诊断实验，不是52个ERP业务任务成功率**。单字符串工具刻意不实现ERP语义；auto的HTTP200也必须核验是否返回实际工具调用。

## 全部52次结果

| 诊断变体 | 请求数 | HTTP/流正常 | 同类编译超时 | 解释 |
|---|---:|---:|---:|---|
| 当前5002字节完整定义，强制指定工具 | 10 | 9 | 1 | 未精简也能稳定多次完成，但会间歇失败 |
| 精简9字段2504字节，强制指定工具 | 10 | 9 | 1 | 精简字段未消除该异常 |
| 仅一个字符串参数、开放对象335字节 | 9 | 8 | 1 | 字段多不是必要复现条件 |
| 完整定义，仅限定3处value类型 | 4 | 4 | 0 | 样本不足以证明根治或显著降错 |
| 完整定义，仅关闭object额外属性 | 4 | 4 | 0 | 同上；不能直接上线关闭全部扩展字段 |
| 单字符串，关闭额外属性 | 3 | 3 | 0 | 最小封闭定义本轮未复现 |
| 单字符串，显式strict=false | 3 | 3 | 0 | 与未设置strict的样本量不足以证明因果 |
| auto（完整定义6次+单字符串3次） | 9 | 9 | 0 | 完整定义6/6没有tool_calls；单字符串封闭定义3/3有工具调用，但不实现ERP合同。**不能作为ERP修复** |
| 合计 | 52 | 49 | 3 | 三次失败全部保留，无静默重试 |

3次失败均为HTTP400，error.type=invalid_request_error，error.code=invalid_parameter_error，message相同：

`<400> InternalError.Algo: An error occurred in model serving, error message is: [grammar validation or compilation failed: sandbox grammar compile timed out]`

| 变体 | 耗时 | 原生供应商request_id |
|---|---:|---|
| 单字符串 | 30.65秒 | 053fc683-e44d-923f-af19-300351aa34b4 |
| 9字段 | 30.52秒 | d44b595b-f5a1-92e0-99d0-5f9906213e53 |
| 当前完整定义 | 30.53秒 | 317458e6-925a-9d2a-ae77-a8cdc904c9b6 |

单字符串同一规范化请求体既有约3–6秒完成，也有30.65秒编译超时；另一次31.75秒返回正常工具参数。不能把总耗时全部视为模型“思考”，也不能据HTTP400错误码推定用户传参必然格式错误。

## 官方文档核对与适用边界

- [百炼OpenAI兼容Chat参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)：tool_choice为auto或强制指定函数是不同模式；strict是单独的可选属性。文档没有为本次具体sandbox错误给出明确的schema复杂度阈值或根因说明。
- [DeepSeek工具调用说明](https://api-docs.deepseek.com/guides/tool_calls/)：strict模式有专门启用方式和支持子集；严格object规则包括所有属性required、additionalProperties=false。**我们实际请求没有启用strict，而且走的是百炼端点，不能直接用DeepSeek直连Beta strict规则判定当前请求非法。**

没有把网上第三方issue或其他版本模型的异常当成本次根因。官方文档支持接口语义，不支持“已证明供应商内部某一组件故障”的更强断言。

## 建议及当前边界

- 保留当前后台的scope、时间列、组织权限、结果校验和失败不执行原则；不添加词表或第二个纠错模型。
- 不直接改为auto：本轮完整ERP定义6/6没有工具调用，HTTP成功会掩盖业务不执行；单字符串封闭定义的3次工具调用不能代替原ERP功能。
- 对value类型、重复筛选定义和扩展字段范围可以做最小契约收敛，但要先覆盖原有全部ERP参数/依赖字段，再用相同真实失败请求对照。该优化有价值，**尚未证明能根治本次编译超时**。
- 不一概重试所有invalid_parameter_error，也不丢口径/日期/筛选来换“成功”。若后续处理重试或供应商兼容性，只能依据明确错误类型和有界策略另行验证。
- 更深的内部原因必须依据最小请求和原生request_id核查。未向供应商或任何人发送消息。

## 证据

- `/private/tmp/grammar-causality-audit-20261125/`：第一阶段实际HTTP请求、结果和合并统计。
- `/private/tmp/grammar-causality-audit-20261125-stage2/`：当前/9字段/单字段的真实失败、原生request_id、完全相同规范化请求体指纹。
- `/private/tmp/grammar-causality-audit-20261125-stage3/`：单字段边界及新建客户端实验。
- 每组有`*-actual-request.json`、`results.json`和`run.log`。所有内容均是测试输入，无API密钥；目录权限700，证据文件600。
- 业务代码未变，未运行部署、迁移、提交或回退；本轮不重复上轮2044项业务回归，因为没有业务代码修改。


## 后续：百炼官网逐项指定核对

本轮仅查询官网及核对留存请求，没有修改业务代码或新增付费模型测试。

- [百炼Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)：列出deepseek-v4-pro支持工具调用；强制调用章节明确以具体模型支持为前提，示例使用Qwen，不能推导所有模型稳定支持全部取值。
- [Chat参数参考](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)：指定函数的对象语法与我们的实际请求一致；enable_thinking=false适用于阿里云直供DeepSeek-V4，HTTP直接调用放顶层正确。页面对deepseek-v4.1-flash-1017明确列出全部tool_choice取值，对当前V4-Pro别名没有同样逐项说明；这既不能当成明确支持保证，也不是明确不支持的证据。
- tool_stream仅限该页列出的Qwen和GLM型号，不能将它对复杂参数超时的说明套给DeepSeek。实际没有传strict或response_format，不能以另一协议或严格模式的要求判定这次请求非法。
- [官方错误码](https://help.aliyun.com/zh/model-studio/error-code/)中“tool_choice只允许none/auto”的条目针对该具体报错；我们收到的是grammar编译超时，两者不相同，不能据此直接改auto。

结论仍是：未查到当前请求违反某条明确的百炼参数规定；强制调用的型号兼容性仍需审慎处理。官网未给出本次sandbox grammar compile timed out的明确根因或专用解决参数，不能用官网替代内部trace完成归因。

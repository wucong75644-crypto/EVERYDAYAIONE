# 工作区搜索后分析失败：生产诊断

状态：下方为 2026-09-10 对候选 bb4449a0e53b6c3b1d11aaa98e1c1faa410a81aa 的只读诊断快照；诊断未执行生产分析、删除、恢复或数据写入。后续根因修复已在原任务工作树实施，2960 项相关测试通过，2 项既有真实模型测试未验证；尚未提交部署，不代表生产已修复。

实际接口、权限来源、Actor 恢复及边界见 [工作区资源选择与授权衔接](TECH_工作区资源选择与授权衔接.md)，基线 4 项失败/修复后成功及逐项证据见 [04 最新验收](TOOL_UNIFICATION_ACCEPTANCE_04.md)。以下因果链描述修复前代码，不能用作当前符号实现的说明。截断数量误述与长引用问题仍留待 05/06，不包含在本次已修复结论中。

## 真实输入与调用轨迹

对应会话 6033131c-d370-43a5-bada-33c392196ef6，失败任务 f7f9cb8a-1a58-50c5-907f-8a9e31b3c6e1，13:29:19–13:29:54。

截图顶部是早先删除请求的会话标题，不能当作本轮指令。本轮用户输入为「列出当前工作区的文件”，再读取一个获准测试文件」。此前 13:28 的图片删除结果为成功；同会话随后带附件的 file_analyze 也成功。未检查底层存储历史以独立证明此前删除的所有副作用。

| 步骤 | 实际参数 | 结果 |
| --- | --- | --- |
| 1 | file_search(scope=workspace) | 返回工作区根目录 10 个目录 |
| 2–5 | file_search(path=上传/、已整理表格/、财务数据/、运营文档/)，均无 scope | 每次返回「当前任务资源中未找到匹配文件」 |
| 6 | file_search(file_pattern=*.xlsx, scope=workspace) | 返回前 100 项，尾部明确提示结果不完整 |
| 7 | file_analyze(resource_ref=搜索结果中的引用)，无 scope | RESOURCE_PATH_NOT_IN_MANIFEST，未执行业务 |
| 8 | file_analyze(file_id=另一个候选 ID)，无 scope | 同一范围拒绝 |
| 9 | file_search(scope=current) | 当前任务没有匹配附件 |
| 10 | file_analyze(path=工作区候选相对路径)，无 scope | 同一范围拒绝 |

模型最后误述「必须添加到当前任务才能读取」，也将截断的前 100 项误述为 Excel 文件总数。不能将模型自行称作「获准测试文件」视为新的资源授权事实。

## 因果链与边界

- config/file_tools.py 对 search/analyze 声明默认 current；services/tools/file_calls.py:resolve_file_call 实际执行同样默认值。每次模型调用须独立传 scope，先前搜索的 workspace 不会继承。
- services/file_resources.py:FileTargetResolver.guarded 在 current 下检查本轮 manifest.allowed_paths。该轮没有附件，三个分析调用因此在解析目标阶段被挡住，先于分析 Handler。签名引用校验后也必须再检查范围；引用不是权限。这次不是 URL/路径丢失，也不是 Excel/Parquet 转换错误。
- 同一缺口同时影响目录搜索和分析：模型一旦漏传 scope，当前附件搜索的空结果被当作工作区目录没有内容；换文件、换 ID、换路径没有修复范围。
- services/handlers/chat_tool_result_mixin.py:_process_tool_exception 给模型的反馈为普通失败文本加内部错误码，没有明确返回实际范围、要求的资源范围和可采取的纠正动作。当前循环允许继续选择其他文件重复失败。
- 生产搜索结果长 38199 字符，其中 100 条引用行合计 32327 字符；同轮模型 prompt 从 9035 增至 37686 tokens。长引用及大列表确实扩大了上下文；没有证据证明它单独导致本轮漏参。
- 截断提示存在但模型仍声称总数，属于结果消费问题。不能通过把 current 默认改成 workspace、自动把搜索命中加入授权或放宽 manifest 检查来解决。

## 推荐修复边界与验证

在已认证的本轮上下文、资源选择及工具调用契约层处理范围衔接：区分资源身份、用户批准的资源范围、模型提出的选择；搜索后的操作携带可验证的选择来源和明确有效范围，始终与本轮授权求交。目录和文件使用一致语义；前次搜索不是新的授权。范围失败返回可理解且可纠正的信息，无授权时明确停止，不能靠换参数绕过。

增加真实循环中「workspace 搜索后省略 scope 的目录/引用/ID/路径操作」验证，同时覆盖拒绝、纠正、current 附件、群 owner、定时范围和确认恢复。此前测试主要手工构造正确 scope，完整搜索接删除测试的 delete 默认 workspace，不能证明默认 current 的 analyze 衔接可靠。此次生产复现须转为回归，不能只加强提示词后宣布根治。

搜索分页、短引用、截断结果与数量表达作为关联问题单独评估；本次未修改结果显示或 replay 格式。方案涉及可信资源选择契约的具体设计，尚未在本轮实施。

## 证据与验收

原始只读材料保存在本机 /private/tmp/tool04-sep10-production-diagnostic.log 和 /private/tmp/tool04-screenshot-conversation.json，含生产内容，不提交仓库；本记录只保留定位信息与必要因果证据。

bb4449a0 的受控发布成功；前端 1309 passed，后端 9149 passed、37 skipped、4 xfailed，构建与健康检查通过。这些结果不覆盖当前真实模型调用缺口。板块 04 的本次用户验证失败，不能保持总体「技术通过/可关闭」结论；已有安全拒绝和其他测试证据仍有效，须补齐本场景修复与复验后再更新结论，不进入 05。

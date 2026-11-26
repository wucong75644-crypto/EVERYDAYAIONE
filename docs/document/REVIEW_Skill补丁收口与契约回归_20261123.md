# Skill 补丁收口与契约回归

> 本文保留当时实现和验证事实。2026-11-25 起，编辑参数的现行合同改为[统一编辑参数](TECH_图片编辑参数统一与场景验证_20261125.md)，不再向新请求提供 previous_result_edit。

## 范围与基线

用户确认先审计近期补丁，撤回过度设计，按只读诊断方案实施；不部署。本轮仅处理近期修复增量，不整体回退长期工作树，不改用户主工作树。

修改前副本：`/private/tmp/skill-approved-simplify-before`。更早的业务文件基线：`/private/tmp/skill-supplier-formal-entry-image2-customer1-20261123/business-files-before.json`。旧测试、失败和原型证据保留在私有目录，不以本轮结果覆盖原始结论。

## 审计结论与实际处置

| 原补丁/问题 | 本轮处置 | 理由 |
|---|---|---|
| planner_handoff 自动重试、问号判断和独立检查点 | 删除模块与调用，替换对应测试 | 已抓到的漏调用由适用范围冲突解释，没有足够证据要求另加一轮催调用；标点不能代表业务状态 |
| 手选方法后按主图/详情页关键词走快路径 | 删除快路径，统一走现有适用范围路由 | 不再把词表当作用户完整意图；路由不可用不伪造 main_images |
| explicit_chat_count 词法推断 | 删除，并恢复 service 原默认值处理位置 | 张数是工具规划参数；有限词表不能承担自然语言理解 |
| 首次策划缺张数直接默认10 | 新模型工具视图要求首次显式填写 image_count，原参数修复账本负责未派发纠错 | 用户要求3套不应因漏字段静默变成10。用户未给张数时模型填现有默认10；续接和原 ToolSpec 保持兼容 |
| implicit_generated_image_edit 口语模式、动态 source const 与相关前置读取 | 删除 | 不再通过特定“再换成…”句型强制编辑形态，也不在每轮模型前重新解析图片目录 |
| 需要继续上一轮实际成图 | 增加原 generate_image 的 previous_result_edit 参数形式 | 由模型按语义选择此形式；后台依据唯一原图片回执定位实际输出。指定图片、全新创作、按编号执行仍保留原形式 |
| remove 操作被强制要求 value | 保留修复，并让展示 Schema 和本地校验一致 | remove 可无 value；replace 必须有 value；旧 remove 的 value=null 和省略 op 的 replace 仍兼容 |
| 普通图片方案混入整组总结 | 保留执行片段边界与原文范围机制 | 只针对新写的图片方案，展示间隙原文保留；不做总结语义删除、不增加二次改写模型 |
| 意外无策划回执时吞正文 | 保留正文并标明未登记、未提交 | 不假称服务器方案 ready，也不按问号猜是否应重试 |

后台不新增通用审核器、Agent、审批或数据库表，不改变思考开关，不将诊断信息送入生图提示词。

真实主图首轮又发现两处现有修复契约不一致：schema 校验失败提前返回使张数不足没有一起报告；review_records 既被生成补丁目标又被接收端禁止作为补丁路径。现只汇总已有张数约束，并将 review_records 保留为 DesignRepair 已有的独立顶层字段。原修复次数、字段范围、完整校验和前两阶段保持不变；新增正式回归在修复前失败、修复后通过。

## 连续编辑的准确边界

`previous_result_edit` 是模型工具参数，不是用户要学的新话术。用户仍可说“再换成浅灰色”。

只有现有任务状态里存在唯一图片回执，且本轮没有显式上传/引用图片时，才提供这条快捷形式。模型选择后，后台通过原 native_resolver 的会话范围和历史边界找到该任务的已完成输出；多结果或尚无完成结果时，不退回旧输入图。已有文件、来源、权限、尺寸与内容校验继续执行。

实际编辑正文按模型写稿保留，后台不改写。原始调用参数哈希与编译后的图片请求哈希分开保存。恢复使用已冻结的图片请求，不重新解析“最新图片”，不新增受理。

这条形式没有替代原先指定图片的 source_message_id/content_index 或 draft，也没有替代编号方案的 content_source/plan_source。客户端协议没有变化。

## 验证证据

以下计数有重叠，不相加，条件跳过不计成功。

- 最终主回归：1128 passed / 22 skipped，覆盖聊天、Skill、文字登记与编号引用、运行中追加、权限、旧方案及恢复。私有日志：`/private/tmp/skill-simplify-all.log`。
- 图片生命周期、并发与恢复：158 passed / 3 skipped，日志：`/private/tmp/skill-simplify-extra.log`。
- 显式独立 PostgreSQL 检查点：3 passed，日志：`/private/tmp/skill-simplify-checkpoint.log`。
- 收口后的 PostgreSQL 交付检查：58 passed / 18 skipped，包含两个数据库角色下的上一轮结果编辑、准备后恢复和受理后恢复。日志：`/private/tmp/skill-simplify-recovery.log`。
- 新增参数测试证明首次策划漏张数不会派发，下一次只补用户实际张数可经原账本执行；canonical ToolSpec 未被改写，原续接参数仍合法。
- 新增执行稿边界测试重建整份可见正文逐字一致，同时确认只把提示词片段交给图片执行；段外约束仍沿原兼容机制保留。
- 新增无策划回执测试覆盖正常停止、澄清候选、长度限制和拒绝说明：正文保留、没有自动加轮、没有伪登记或图片派发。

真实模型与供应商复测使用冻结候选，分别为普通新款设计和专用主图 Skill。证据目录：

- `/private/tmp/skill-supplier-formal-entry-image2-approved-simplified-customer1-20261123`
- `/private/tmp/skill-supplier-formal-entry-image2-approved-simplified-main1-20261123`
- `/private/tmp/skill-supplier-formal-entry-image2-approved-simplified-main2-20261123`
- `/private/tmp/skill-supplier-formal-entry-image2-approved-simplified-main3-20261123`

每个目录包含启动前 services SHA256 清单、正式 HTTP/Actor/追加消息路径、原始模型响应、已保存正文、实际图片输入和供应商结果。结果如下，失败样本未删除：

| 实测候选/链路 | 成功 | 失败/未运行 | 事实与边界 |
|---|---|---|---|
| customer1 普通新款：手选Skill→只写3份→运行中追加→编号1/3→蓝→灰→原双图失败会话 | 完整正式入口用例1 passed，5/5真实Image2作业完成 | 0 | 正文20.138秒；编号受理3.945秒，蓝11.283秒，灰8.898秒；供应商完成73.865–89.128秒/张。哈希证明灰色编辑引用蓝图；目视背景蓝灰变化、主体基本保持，双图给目标本子添加波点。未证明小字像素级保真或统计稳定率。 |
| main1 专用主图，修复契约前 | 前两阶段完成，image_count=3 | 策划阶段失败；后续0次提交 | 第三阶段仅产1图且漏字段；两次局部修复耗尽，最后为PLANNER_REPAIR_SCOPE_CHANGED。由此定位上述两处契约矛盾。 |
| main2 最终候选，专用主图 | 参数Schema和调用有效，返回needs_input | 数量断言失败，后续0次提交 | 用户要3套，模型显式传10；用户原话与追加已在模型上下文，不能归咎后台漏参默认。失败在业务含义，不放松张数断言。 |

main2首次启动另发生测试环境DATABASE_URL/JWT_SECRET_KEY未配置的收集错误，未调用模型；补测试环境后才得出上表真实结果。独立保留collection-error日志，不当作供应商失败。

最终候选 main3：1 failed /160.18秒，模型仍显式提交image_count=10，后台在156.844秒后ready并保存10项。预期3项的严格断言失败，后续按编号出图及编辑未运行，0笔供应商作业。此轮确实覆盖了三阶段策划及原生pending恢复，并不等于用户需求完成。

最终候选真实主图两轮均在张数含义处失败（main2为needs_input已保存10，main3为ready10），不能把缺参数不再默认10冒称已解决“模型显式填错10”。原始3套要求和追加内容均在上下文；现有Skill说“用户要求的张数”，工具默认10，套方案与逐图数量的契约仍需明确。不新增词法推断、全局语义审核或自动催调用来掩盖失败，也未改测试要求或换模型。

customer1之后仅修改designs.py的修复契约，customer1不是最终文件哈希完全相同的一轮；该文件不参与普通新款执行，不能据此冒称最终双链路全部成功。

本轮无提交、推送或部署。没有把局部和离线通过当作线上或完整真实出图验收。

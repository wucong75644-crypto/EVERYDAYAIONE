# Skill 真实图片供应商闭环验收

2026-11-22。本轮在原任务工作树继续，未提交、未部署。后台业务差异沿用上一轮；本轮补充真实付费入口，并修复了前端解析尺寸质量字段的一处实际遗漏。

[原始失败、Grok 源码对照和职责](TECH_Skill真实失败链路修复与Grok对照_20261122.md) · [上一轮模型与回归记录](TEST_Skill真实失败修复验证_20261122.md)

## 结果

使用项目实际 KIE `gpt-image-2-5-sunburst-image-to-image` 适配器、真实 HTTP、真实 OSS 原图/缩略图上传和 CDN。没有用生成替身或示意图替代供应商返回文件。任务、消息、资产、积分、检查点与事件使用独有 socket-only PostgreSQL；限流与锁使用独有无 TCP Redis。已有线上任务和数据没有修改，OSS 只新增本轮独有测试用户目录的验收对象。

| 原场景/动作 | 实际结果 | 实际像素 | 输出规格 | 提交至下载发布耗时 | KIE自身 costTime |
| --- | --- | --- | --- | --- | --- |
| 参考图一波点，给图二添加 | 星星、文字与主要构图保留，图二新增浅灰波点 | 1086×1448 | 3:4 / 默认1K | 65.058秒 | 59秒 |
| 原五方案中的第1条 | 牛仔拼接、铆钉、米白压纹封面 | 1254×1254 | 1:1 / 默认1K | 66.459秒 | 58秒 |
| 原五方案中的第3条 | 格纹蝴蝶结、银色爱心扣、展开内页 | 1024×1024 | 1:1 / 默认1K | 175.317秒 | 164秒 |
| 基于第3条实际结果，把背景改浅蓝 | 使用第3条真实生成文件，保留主要主体与排版 | 1024×1024 | 1:1 / 沿用1K | 401.851秒 | 394秒 |
| 基于浅蓝版实际结果，把背景改浅米灰 | 使用浅蓝版真实文件，主要主体与排版保留 | 1254×1254 | 1:1 / 沿用1K | 70.546秒 | 62秒 |

供应商实网完整场景验收 **1 passed，505.25秒**。这次最终运行复用了已经完成的前三张真实付费结果，再生成两张连续改图；没有为验收程序修正而重新提交同一个已受理图片。全部完成后从私有快照再次恢复完整闭环，**1 passed，5.43秒，新增供应商提交为0**。

五张主验收图均逐张人工查看，并通过真实 CDN 原图与缩略图 HTTP 200 检查；原图 CDN 字节 SHA-256 等于本地供应商下载文件。第3方案采用多视图排版，后续两次编辑保留该排版。存在生成模型的细节重绘，不能声称像素完全不变。

**1K 是供应商分辨率档位，不是承诺固定1024像素。** 本轮同档返回1024、1254以及1086×1448；比例和已授权档位正确，未靠裁切、拉伸或降低尺寸断言来通过。用户没有要求固定像素；明确像素的现有拒绝/确认机制没有修改。

## 编号、引用、权限与恢复如何核对

- 方案场景使用真实失败会话的五条原文，逐字回放到后台登记、保存、关闭前回合，再经过真实下一回合入队/认领和 adopt/capture。此次没有另写一组方案替换原稿；上一轮的新方案真实模型6组复测仍见原记录。
- 第1、3条的原生 `prompt` 逐字等于对应原条目；进一步对照实际供应商 POST 的 input.prompt 与冻结快照，五次均一致。没有让模型再保存、摘要或拼接原稿。
- 双图场景的已认证目标为图二，原像素1280×1707最近匹配3:4。模型把图一用于分析，波点要求写入编辑正文；实际供应商执行参考为图二。没有把“分析素材也已传给生图工具”当作验证结论。
- 两次连续改图均严格断言执行引用的 message_id 等于上一张真实结果消息，content_sha256 等于上一张PNG字节摘要；第二次没有退回最初上传图或最早生成图。比例与分辨率档位继承，真实文件和来源校验没有放松。
- 原生派发仍经 ToolPolicy、作用域文件解析、TaskOperationStore、ImageHandler、原生受理和不可变快照。限流与完成锁使用真实本地 Redis；每个子任务均恰有一笔 confirmed 积分记录和已登记资产。
- 图片发布后，调用实际 ImageContinuationConsumer，重复扫描验证同一待办只创建一个接续回合；真实认领、加载、apply、模型收尾和 terminal RPC 完成，最终逻辑任务状态 completed。接续禁止新增业务效果，没有再次生图。
- 本轮留存的11个模型 HTTP 请求均为 `reasoning.effort=none`；没有改回 low。

图片服务返回200并完成真实创建后，后续重复检查只查询同一个供应商任务号。KIE 原始查询对五个主任务均为 success，且记录真实开始/完成时间。供应商394秒的那一轮一直处于 generating，没有后台反复保存或重复提交。

## 本轮发现并修复的实际前端缺口

真实 worker 事件接到前端后，5条图像回归均复现：`normalizeMessage → parseContentParts` 会把 `quality_checks` 中的 size_matches、actual、target 和 error 丢掉，剩空对象。原类型已经声明部分尺寸字段，运行时 Zod 契约却仍仅认识透明度/alpha 字段；文件本身可以显示，质量结果不能完整穿过页面与刷新。

修复在 `frontend/src/schemas/messageProtocol.ts` 补齐已有后端字段的明确类型，并同步 `ImagePart` 的目标来源元数据。没有用无条件 passthrough 接受任意质量字段；原透明度检查保留，新增布尔/实际像素校验，不把字符串false当布尔。

脱敏的5对真实 message_pending/message_done 进入正式 fixture `chat_skill_real_supplier_delivery_20261122.json`。新增默认执行的5项实际 Store/消息处理回归、5项真实组件显示/点击/持久化刷新回归，以及质量证据的真假值/非法像素检查。显示保留全部尺寸与来源字段；迟到 pending 和重复追加使用同一消息槽位，原父聊天流不被关闭。

## 检查结果

| 检查 | 结果 |
| --- | --- |
| 真实供应商5场景，含两轮连续改图 | 1 passed / 505.25秒；真实已完成结果跨运行复用，不重复扣费 |
| 已完成快照再恢复，不新建图片 | 1 passed / 5.43秒；新增供应商POST=0 |
| 页面事件、实际 Store、组件、刷新、协议定向 | 123 passed / 4既有条件跳过；新增11项全部执行 |
| 前端全量 | 245文件，2,795 passed / 4既有条件跳过，43.88秒 |
| TypeScript、生产构建、PDF worker | 通过，构建12.01秒 |
| diff 检查 | 通过 |
| ESLint | 生产协议/类型和新增断言无新增问题；原 wsMessageHandlers.test.ts 的14处既有any，逐条与HEAD基线比较一致，未用禁用规则掩盖 |

本轮后台业务代码没有再变更，上一轮同一后台差异的5,240项及独立PG/Redis135项证据继续有效，不冒称本轮又重跑了一次。新付费入口不是默认CI测试文件，须显式授权开关及私有配置；真实事件脱敏后的11项普通回归默认执行，不依赖供应商或付费开关。

最初验收程序继承了两个数据库fixture参数，因此额外生成了一张双图场景图片。其后改成显式单角色入口。两个最初的图片都已真实成功；原生上传、登记、发布和结算已完成，但只读审计误用应用角色直接读受控资产表导致测试失败。随后修正为隔离库所有者只读审计，未授予应用新权限，并补齐最小fixture缺少的Actor终态、续约函数生产所有者、入队字段和真实接续驱动。早期脚本错误不当作产品失败，也不隐去重复付费记录。

KIE原始回执核对：**共6个真实生图任务全部success，各6 credits，总36 KIE credits**。其中5个主验收任务30 credits，初始额外角色复验6 credits。应用积分账本是隔离库数据；聊天模型费用另计，没有据此推算人民币或读取生产账单。

## 产物与复跑

- 结果总览：`/private/tmp/skill-supplier-20261122-complete/results-overview.png`，五张原始结果的逐字节副本在同目录 results。
- 完整私有请求、受理、冻结快照、真实文件摘要、事件：同目录 evidence.json；独有数据库快照 isolated-database.sql；供应商最终状态 remote-audit.json；真实 CDN 检查 cdn-audit.json。
- 完成批次日志：`/private/tmp/skill-supplier-completed-input/paid-five-scenarios.log`；已完成结果零付费恢复日志：`/private/tmp/skill-real-supplier-idempotent-final.log`。
- 前端失败复现与修复：`/private/tmp/skill-real-supplier-frontend.log`、`/private/tmp/skill-real-supplier-frontend-fixed.log`、`/private/tmp/skill-real-supplier-frontend-full.log`、`/private/tmp/skill-real-supplier-build.log`。
- 显式付费入口：`backend/tests/manual_skill_supplier_acceptance.py::test_real_incidents_and_two_consecutive_edits[everydayai]`。必须提供 RUN_SKILL_REAL_SUPPLIER_ACCEPTANCE=1、SKILL_SUPPLIER_SETTINGS_FILE、SKILL_SUPPLIER_EVIDENCE、独有 CHAT_IMAGE_TEST_REDIS_SOCKET；首次场景需私有原始素材 SKILL_FAILURE_CAPTURE_ROOT。恢复必须提供同一次私有 evidence/database 快照及对应 SKILL_SUPPLIER_WORKSPACE，避免创建替代任务。

上述私有配置、数据库快照和真实请求不入仓。验收照片/真实OSS对象仅新增于本轮独有命名空间。浏览器线上登录、人工业务验收和部署未进行；本轮已经补过的范围是本地真实模型/原稿、真实图片供应商、真实文件/数据库/恢复以及正式页面回放。

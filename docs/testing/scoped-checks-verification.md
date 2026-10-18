# GitHub 分档检查验证记录

验证日期：2026-10-18（北京时间）。7 个正常样例通过，2 个故意失败样例均被正确拦截。没有生产部署、生产凭据访问、main 合并或工作树清理。

隔离样例基座：`94631945f2acb5062dc26d5cff13b02c3e57dd10`。正式任务代码提交：`c134f6c42a5a14c1ac59668f68cf2cefd9f06544`，其[完整检查](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110679525)通过，含排队总耗时 250 秒。后续新增的本报告不改变应用或检查实现，但提交后 SHA 会变化；真正部署时仍须按最终 SHA 重新检查，不能复用旧 SHA 结果。

| 样例 | 前端 job | 后端 job | 汇总门禁 | 含排队耗时 | GitHub run |
| --- | --- | --- | --- | --- | --- |
| 普通文档 | skipped | skipped | success | 18 秒 | [39110694764](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110694764) |
| 普通前端 | success | skipped | success | 96 秒 | [39110696528](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110696528) |
| 映射后端 | skipped | success | success | 167 秒 | [39110698323](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110698323) |
| 前后端混合 | success | success | success | 162 秒 | [39110700817](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110700817) |
| 核心模块 | success | success | success | 230 秒 | [39110702645](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110702645) |
| 未知路径 | success | success | success | 199 秒 | [39110704877](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110704877) |
| 影子模式 | success | success | success | 243 秒 | [39110706370](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110706370) |
| 故意测试失败 | failure | skipped | failure | 51 秒 | [39110708672](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110708672) |
| 故意分类失败 | skipped | skipped | failure | 19 秒 | [39110710396](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39110710396) |

## 核验内容

- 普通文档仅基础检查；前端相关测试和构建；映射后端定向回归；混合两端均运行；核心、未知与影子模式前后端完整运行。上述均为同一工作流、同一分类器和执行器的真实 GitHub 运行，不是只运行分类单元测试。
- 每个样例核对完整候选 SHA、比较基座、计划指纹和真实 scope/frontend/backend/required job 结果。
- 正常样例的真实 job 元数据逐项删除必要项或改成 skipped，再调用发布门禁的计划核验函数，均被拒绝。这部分是对真实结果的本地故障注入，不声称在 GitHub 上故意删改了 job。
- 每个验证运行均调用正式检查验证入口，确认即使绿色也因 verification-only 被拒绝；将候选 SHA 换成基座 SHA 同样被拒绝。没有为测试拒绝规则创建生产请求。
- “故意测试失败”仅追加一个明确抛错的合成 Vitest 用例；“故意分类失败”仅使用不含凭据的合成禁用文件名。两者的 failure 是期望结果，不能被当作发布成功。
- 前端/后端定向、本地依赖发现回退、超时、取消、结果复用和生产锁回归沿用本任务已完成的本地验证；本轮新增验证请求格式和验证专用运行拒绝回归也通过。

## 运行边界与限制

- 验证标签为 `preflight/verify/*`，只触发无生产 Environment、无生产 secrets 的只读检查工作流；发布入口明确拒绝它们，任务入口也不会将它们复用为正式检查。原任务分支不包含各样例的合成业务改动。
- 验证比较基座为样例唯一直接父提交，不修改 main 或其他任务工作树。正常发布仍比较最新 main；不存在把演练基座用于生产放行的路径。
- 后端 targeted 路径从相同执行器的明确分支返回，不执行 native/bootstrap/服务启动；真实后端 job 成功及分类指纹已核验。当前云端出站策略拒绝 GitHub artifact 的 Azure Blob 下载目标，因此未下载、逐阶段读取 GitHub JSON 报告；不声称已从下载报告独立核对测试计数或服务进程。报告及阶段详情仍在对应 GitHub run 内保留。
- 耗时是本次并行实测，包含排队、依赖、构建和报告收尾。缓存、下载和负载会改变结果；混合档使用并行 job，不能把两端耗时相加或承诺固定时长。
- 首次将 9 个标签一次推送，GitHub 未生成事件；随后修复为逐标签推送，使用新 nonce 完成上述实跑。未触发的旧标签保留作审计，没有删除或重写标签。被后续任务提交替代的旧普通检查取消不计为通过。

## 候选与审计标签

- 普通文档：`65b34fbef26fdd8dcedb782019e2ea541e6467cc`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/docs/0a81520535dd`。
- 普通前端：`3a5aa0d8ed686e60cb7714c331e7e5299689282a`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/frontend/a123b5385217`。
- 映射后端：`c5827f0ccc2196d79746c759d18547f8cb1b8cf2`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/backend/a3ebd49f89a6`。
- 前后端混合：`3590c39557030257cf14ca49851242920710bf73`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/mixed/6c92593179f6`。
- 核心模块：`57db04d5b13b7a70aab004d1a3616382b0b18906`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/core/a7bdf1b6200b`。
- 未知路径：`eefe450d3bd5ce11d8defa25c6fb29252683870a`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/unknown/5487ce338d48`。
- 影子模式：`437e4b81eb810ccba2e846783c994d1691a2f26f`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/shadow/43a69ee2372d`。
- 故意测试失败：`24d6e3916adb36ff6481ead73e27ef416e75611a`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/failed-test/0d1ce5d40844`。
- 故意分类失败：`d79f3183c625b4befebfa73ef6e8f2b3066677bf`；`preflight/verify/94631945f2acb5062dc26d5cff13b02c3e57dd10/failed-scope/443e0ba926e5`。

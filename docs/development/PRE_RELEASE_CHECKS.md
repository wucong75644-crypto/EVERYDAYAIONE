# 可选 GitHub 代码检查

生产已统一走本地 deploy/release.sh；本文描述可选 CI 检查机制，不是生产发布授权或执行入口。不要创建 delivery-production/accept 标签。当前发布流程以 [APPROVED_DELIVERY.md](APPROVED_DELIVERY.md) 为准。下文保留历史检查机制细节供维护参考。

# 发布前 GitHub 检查

日常开发和预览留在独立 Codex 任务工作树。用户说“提交部署”后，入口整合最新 main，对最终完整 SHA 选择检查，再请求生产发布。检查不持有生产凭据，不连接生产。部署和验收规则见 [APPROVED_DELIVERY.md](APPROVED_DELIVERY.md)。

## 检查范围

分类依据是最终候选相对检查时 `origin/main` 的**全部差异**，不是最后一次提交；基座必须是候选祖先。新增、删除、重命名两侧和类型变化都纳入。分类同时执行 `git diff --check` 和敏感路径检查；分类失败直接阻断。

| 档位 | 范围 | 执行内容 |
| --- | --- | --- |
| 轻量 | `docs/` 下普通 Markdown/TXT，以及根目录、前后端 README | 基础检查；不部署应用 |
| 常规前端 | 非核心 `frontend/src/` 或 `frontend/public/` | 相关测试＋TypeScript/Vite 构建；不运行后端检查 |
| 常规后端 | 已审查的 mock 回归映射内的模块 | 独立环境安装、pip check、配置生成、Python 语法检查和映射测试；不初始化数据库或启动服务 |
| 完整 | 核心模块、共享契约、登录权限、调度、依赖、数据库、发布脚本，以及其他未映射改动 | 前后端并行运行现有完整发布前检查 |

前端共享 services、schemas、stores、contexts、types、config、constants、测试启动配置、应用入口及名称涉及 auth/permission/scheduled/protocol 的文件均走完整检查。运行时代码的删除、重命名或类型变化也走完整检查。代码内注释仍按所在代码文件分类，不做可能误判的文本猜测；应用使用的 prompt/skill Markdown 不视为普通文档。

前端通过 Vitest 的导入依赖图选择间接受影响测试，并逐个检查改动输入是否有相关测试。任一输入没有可靠映射时扩大到前端全部测试；发现过程出错则阻断，不能算作“没有测试”。测试自身改动也会选中自身。

后端不按文件名猜测覆盖范围。`scripts/ci/check_scope.py` 的 `BACKEND_MAP` 只登记经审查、使用 mock 且无需外部服务的回归套件，初始为 `ip_location_service.py → test_ip_location_service.py`。其余后端改动保守走完整检查。扩大映射需要核实调用方和测试隔离，并同步回归用例；映射或测试缺失回退完整检查。前后端常规改动混合时两端都运行；任一改动触发完整档则两端完整运行。主动完整检查仍包含已选中的映射测试，不会反而少测。

纯文档候选检查完成后输出 `documentation_only=true deployment=skipped task=retained`，不创建生产请求、不伪造“生产已测试”标记。保留任务供文档审查；现有“生产候选验收关闭”不适用于未部署文档任务，本次不新增绕过生产验收的合并入口。

## 门禁与结果复用

工作流有 `scope`、按需的 `frontend`/`backend` 和始终执行的 `required` 汇总门禁。只允许分类明确不需要的 job 跳过。分类失败、必要 job 缺失/跳过/失败/取消/超时都不能发布。

汇总 job 名称携带基座 SHA、模式和计划指纹。发布入口重新计算候选差异和计划，核对完整 SHA、当前策略摘要、汇总门禁及必要 job，不能仅凭整个 workflow 绿色放行。策略摘要覆盖分类器、执行器、工作流和前端依赖发现脚本。

同一完整 SHA 的有效结果直接复用；已有运行则等待，不重复创建。合入 main、解决冲突或修改代码后 SHA 变化，必须检查新版本。较新的失败运行不会静默退回较旧的绿色结果；排查后可显式重新请求检查。依赖安装遇到明确的临时网络错误时额外重试一次；断言失败、配置错误、超时及生产操作不自动重试。部署执行器继续使用 `--skip-test` 复用检查，保留构建、数据库身份、迁移、服务器锁、生产版本和健康检查。

## 请求模式

默认 `scoped` 使用分档检查。`shadow` 展示拟选范围但仍跑前后端完整检查，适合验证分类；`full` 强制完整检查。两者只增加检查，不降低必要门槛。

```bash
# 用户明确说“提交部署”后，由 Codex 使用默认入口：
python3 scripts/ci/task_delivery.py deploy --authorization '提交部署' --message 'type: description' --file path/to/task-file
# 主动要求完整检查：
python3 scripts/ci/request-check.py --mode full
# 仅请求检查，无生产请求（须有对应推送授权）：
python3 scripts/ci/request-check.py --mode shadow
python3 scripts/ci/request-check.py --mode full
```

`request-check.py` 拒绝脏工作树、非任务分支、未推送或本地/远端 SHA 不一致。每次创建唯一 `preflight/*` 标签，完整/影子模式分别使用 `preflight/full/*`、`preflight/shadow/*`；不改任务分支、不强推。普通业务提交不触发此工作流；流水线自身改动触发自检。Actions 手动运行也可选择模式。

请求成功不等于检查通过，检查通过不等于生产授权。没有固定网址的自动预览。GitHub 页面可查看分类、拟选范围、各检查结果和耗时。

## 完整检查范围与隔离

- frontend：Node 22、`npm ci`、全部 Vitest 测试、TypeScript/Vite 构建。
- backend：Debian trixie/Python 3.12、非 root 私有检出，生成开发配置；校验原生包，在独立空白 PostgreSQL/pgvector 与 Redis 上重复核验 bootstrap，运行原生集成、既有后端定向回归、流水线保护测试、真实 API smoke 和 20 秒后台观测。不是整个后端测试集合，不验证真实外部模型/短信/OSS。

每个 job 是一次性环境，数据库和 Redis 不跨任务共享。只缓存 npm/pip 下载，不缓存 `.env`、数据库、虚拟环境或原始日志。后端报告仅回收安全阶段、耗时、退出码及允许的测试计数；原始日志不上传。报告和范围计划保留 14 天。前端上限 20 分钟、后端 30 分钟，另有阶段超时。冷启动、排队和下载会影响时长，不承诺固定时间。

不同任务检查可并行，生产发布仍由共享队列和服务器锁协调。A 待验收时 B 不能覆盖；A 验收后 B 先整合最新 main，再检查最终版本。稳定基座元数据同步不会自动改其他任务文件。

## 验证与启用

先用典型差异验证 `shadow` 分类和原有完整门禁，再验证 scoped 的缺失检查拒绝、新 SHA 不复用、删除/重命名和混合修改。本次实现包含这些本地临时 Git/隔离测试，默认配置为 scoped；流水线自身变更会触发完整检查。真实 GitHub 运行必须在授权推送后确认，不用本地结果冒充 GitHub 已通过，也不因检查成功自动部署。

历史完整检查冷启动基准：[run 39081059788](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39081059788)，前后端均成功，总计 192 秒。它只代表旧完整检查的一次运行，不是新分档检查的实测耗时。

## GitHub 分档实跑验证

`python3 scripts/ci/verify_scopes.py create --push` 仅在已提交推送、干净的原任务中创建隔离候选和 `preflight/verify/<base>/<scenario>/<nonce>` 审计标签。候选是任务代码的直接子提交，只增加无副作用的测试标记；故意失败样例仅包含合成测试错误或无凭据的禁用文件名。原任务文件、分支及 main 不被改写。

这些标签复用同一检查工作流、分类器和执行器，以已提交任务作为隔离比较基座；比较基座必须是候选的唯一直接父提交。`shadow` 样例强制完整检查。**验证专用运行被发布入口明确拒绝，永远不能授权部署或验收**，任务入口也不会将它们当成可复用的正式检查。

`python3 scripts/ci/verify_scopes.py poll` 核验完整 SHA、真实 job 状态、分类计划和必要门禁；另外使用真实 job 元数据验证必要项缺失/跳过会被拒绝、旧 SHA 不能复用。状态记录在忽略目录 `.cloud-dev/ci/scope-verification.json`，可恢复等待，不重复创建请求。它不创建任何 delivery 标签，不访问生产环境或修改生产开关。

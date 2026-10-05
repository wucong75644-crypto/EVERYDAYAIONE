# AOCI 与 EVERYDAYAIONE 任务生命周期

状态：接入入口及首次 MCP Onboarding 已完成；发布仍须按受控入口重新核验 Verify/Check aligned。

## 目标及边界

固定 AOCI 0.1.0-rc17，共用已验证程序，各工作树独立绑定 MCP 和运行状态。
新对话先读当前代码对应的完整认知索引，新增能力前确认已有实现，代码验证后维护索引。
不替代源码检查、测试、项目发布授权或受控入口；无自动生产部署或跨聊天消息发送。

## 本机准备

已校验 macOS arm64 发布包 SHA256，并通过 gh attestation 验证官方 release workflow、tag 和 GitHub hosted runner 来源。
本机工具路径：`/Users/wucong/.local/share/aoci/0.1.0-rc17/aoci`。
其他机器需下载并验证同版本对应平台包。自定义路径使用 `AOCI_BINARY` 或本地 Git 配置 `codex.aociBinary`，要求绝对路径。
辅助脚本需要 Python 3.11+（标准库 tomllib），Git，已校验的 AOCI；没有新增业务依赖。

初次建立只在独立任务工作树执行官方 `init --locale zh-CN --agent codex --scope-profile production`。
首次 scan 前审核 Scope：业务代码、配置契约、迁移和当前接入说明进入 index；历史 docs 和助手配置作为 observe 证据，测试沿用 production 策略；秘密、依赖和生成文件保持安全排除。Observe 仍可按任务阅读和检查变更。
首次 scan 之后必须通过已经连接的 AOCI MCP 按当前 live Guide 生成语义，不能从文件名或 AST 自动拼凑。
正式索引可能较大，应在完成后记录实际 token 数和新对话加载时间；不以调高预算代替范围审查。

## 任务闭环

1. `task-worktree.sh start` 从最新 origin/main 继承正式索引及治理资产，为新工作树生成忽略的 `.codex/config.toml`；准备失败保留任务，不能宣称 AOCI 就绪。
2. 对话每次开始/继续先运行 `python3 scripts/aoci-task.py session --repo "$PWD"`。返回 2 表示稳定更新尚未吸收或索引需维护，1 表示配置/依赖故障；0 仅表示本地检查通过，不能证明模型已经理解。
3. 核验实际 MCP root/version；读取 Rules/live Guide/完整 Overview。MCP 未加载时重新打开该工作树会话，是否需要应用重启取决于客户端实际暴露的工具。
4. 在安全检查点合入通知的稳定提交，保留任务修改；冲突由真实源码、任务设计和 AOCI live Guide 处理。基线是正式治理证据，不能选择 ours/theirs 或重新 scan 来绕过漂移。
5. 完成业务代码和必要验证后用 MCP 增量维护，并通过 Verify/Check。
6. 发布明确包含变更的正式 AOCI 文件。release.sh 在初次提交、自动合入 main、确定候选检出和验收合并检出核验对齐。合并后未对齐则保留合并提交并停止发布，维护后按原受控入口重新提交部署。
7. 验收只提升已测试代码树和索引。稳定同步记录 `codex.taskStableBase` 和 `codex.aociStableCommit`；其他任务的文件/HEAD/index 原样保留，下次 session 检测 pending。

首次接入未验收进入 main 前，其他任务不会继承新入口。已存在的旧工作树需在安全检查点合入包含接入代码的稳定提交，然后生成它自己的配置并重新打开会话；不得从本任务复制配置/基线。运行中聊天不会因 Git 标记自动注入提示词。

## 资产与关闭

提交 `aoci.txt`、`aoci.meta.txt`、`aoci.code.txt`、`.aoci/.gitignore`、`.aoci/config.json`、`.aoci/baseline.json` 及存在且需要的 curation/database 正式资产。
不提交本机 `.codex/config.toml`、其备份、草稿、锁、账本或报告。
辅助程序只核验和绑定，不写 FRAS、不伪造 aligned、收据或模型认知。
可选压缩 hook 未启用；依据项目规则在上下文压缩后重新加载 AOCI。启用前需验证桌面宿主兼容性及完整 compact_prompt 覆盖行为。

## 验证及回滚

定向测试覆盖分支独立配置、已有配置保护、稳定通知不覆盖修改、索引未对齐拦截、缺失资产及未启用项目兼容。发布生命周期回归使用临时本地 remote/伪 SSH，不访问生产。
MCP 首轮构建、新对话完整读取、压缩恢复和实际加载成本需在宿主加载服务器后验证，不能由脚本测试替代。
2026-10-05 本工作树实测：MCP 0.1.0-rc17 的 runtime_repository_root 精确匹配任务根；完整索引 1436 条、约 165346 tokens，分 24 块交付。此次压缩恢复的 24 次 MCP 交付调用合计约 96 秒，不含模型阅读、推理及 Attestation 时间；该数值不是其他机器或新会话的总耗时保证。交付确认成功、Challenge 10/10，Verify/Check 及 live Guide 达到 aligned；空文件与二进制共 24 项由工具确定性跳过，数据库源未配置。当前 v2 投影仍将模型认知可用性标为 false，严格 Challenge 通过与治理对齐不能替代该独立状态，后续工程继续核对源码。
退出接入时保留 Git 历史，并按官方 uninstall 流程处理受管规则与资产；撤销 helper、start/release 调用后才能关闭发布核验，不能仅删除 baseline 绕过检查。

官方来源：
- https://github.com/aoci-spec/aoci-code/blob/main/docs/agent-integrations.md
- https://github.com/aoci-spec/aoci-code/blob/main/docs/managed-scope-and-budget.md
- https://github.com/aoci-spec/aoci-code/blob/main/docs/install.md

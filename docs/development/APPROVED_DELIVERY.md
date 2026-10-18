# Codex指令发布与隔离演练

用户在当前Codex任务说“提交部署”就是该任务本次发布授权；说“清理工作树”就是当前已测试生产候选的验收授权。无需再去GitHub点击Review deployments。普通开发、提交、测试通过、引用本文示例，以及“仅配置/演练”都不是生产授权。

## 日常使用与确定候选

Codex先按实际diff生成精确文件清单和提交信息，然后调用：

```bash
python3 scripts/ci/task_delivery.py deploy --authorization '提交部署' --message 'type: description' --file path/to/task-file
# 已无新改动时不传message/file
python3 scripts/ci/task_delivery.py deploy --authorization '提交部署'
```

该入口只在独立codex/task/*工作树运行：通过git-push.sh提交任务范围，fetch最新main与任务tip，合入main，推送确定SHA，等待同SHA的范围分类、汇总门禁及所有必要检查成功，再创建唯一production请求并等待结果。检查分档及主动完整/影子模式见 [PRE_RELEASE_CHECKS.md](PRE_RELEASE_CHECKS.md)；纯文档候选只完成检查并保留任务，不创建生产请求或冒充生产验收。没有检查时才创建preflight标签；已有进行中检查不重复请求，失败/取消/超时不算成功，不自动退回旧SHA。

合并冲突保留现场并停止脚本。Codex继续在同一授权内理解双方修改、解决冲突、定向验证并提交，再重跑入口；不能用ours/theirs、reset或清理强行丢弃任何一方。无法从现有需求判断的业务冲突才向用户澄清。合并后的SHA必须重新检查。队列期间main或任务tip有变化时runner拒绝旧候选；Codex核验失败原因后重新整合、检查并请求，不盲目重跑可能已写入的生产操作。

## Codex授权与GitHub信任边界

`delivery.py request --mode production --authorization '提交部署' --check-run ...`与accept的对应原话参数是防误触校验，不是对聊天内容的密码学证明。真正的用户指令由Codex对话遵守；GitHub无法读取并验证自然语言对话。任何人如果已拥有仓库写权限，都可能修改工作流或创建标签，因此**仓库写权限、工作流修改权限和配置的请求身份属于生产发布信任边界**。本方案适用于当前个人仓库的可信写入者；不声称取消reviewer后仍能对恶意仓库写入者提供独立人审隔离。

production和delivery-rehearsal环境移除required_reviewers及等待计时器，保留custom tag policies和can_admins_bypass=false。production仅允许delivery-production/*/*/*/*、delivery-accept/*/*/*/*、delivery-verification/*/*；rehearsal仅允许delivery-rehearsal/*/*/*/*。plan和execute同时校验策略、GITHUB_ACTOR/GITHUB_TRIGGERING_ACTOR及GitHub run API的actor/triggering_actor必须与DELIVERY_APPROVER匹配（历史变量名，现在表示可信请求身份；当前为仓库所有者）。不同身份明确失败，不扩大令牌权限绕过。

普通push和检查成功不自动部署。只有用户明确授权后由Codex创建的delivery标签才表达操作请求。标签绑定模式、完整SHA、成功检查run ID、任务名和随机请求ID；GitHub workflow源必须为该SHA。生产总开关PRODUCTION_DELIVERY_ENABLED必须为true。production环境的专用SSH凭据仍不进入Codex环境、普通检查或rehearsal作业。

## 排队、防覆盖与验收

发布/演练共享everydayaione-production-delivery队列，accept使用独立everydayaione-production-acceptance队列，均queue:max、cancel-in-progress:false。按进入队列时间FIFO、最多100个等待请求，不用新请求替换等待项。验收独立队列避免B占着发布队列时阻塞A验收；两个实际写入者仍用同一个服务器锁，锁忙明确失败并保留现场，不抢锁/删锁/自动重试不确定写入。

执行前和锁内复核同SHA检查、远端任务tip、最新main以及生产版本标记。当前生产必须可追溯并是候选祖先；未验收候选只允许原来源任务继续修复，其他任务不能覆盖。未知标记、代码落后、失败或锁所有权不确定均拒绝。A发布待验收时B继续开发；A验收实际完成后，B自己的deploy入口整合最新main，最终版本是稳定代码加B修改。

用户确认线上测试没问题并说“清理工作树”后，Codex停止本任务已核实的开发进程、保留其他任务，再调用：

```bash
python3 scripts/ci/task_delivery.py accept --authorization '清理工作树'
```

accept不提交新修改、不重新整合候选、不部署。GitHub runner调用release.sh --accept-and-close，核对生产标记、候选和最终main树一致后推main，关闭runner临时工作树。原Codex进程等待该acceptance成功后，复核成功run、原任务HEAD/远端tip/干净状态、main包含候选且树一致，再同步本Git仓库其他任务的稳定基座元数据并关闭原任务。不跨VM直接改写B的工作区；B发布前fetch。

若等待中断但远端已经成功，不重复accept；在原任务用 `python3 scripts/ci/task_delivery.py finalize --run <成功acceptance-run> --authorization '清理工作树'` 安全收尾。若任务后来有新修改、main变化混入其他代码或验收作业失败，原任务保留。旧本地入口/人工SSH不在此自动保证中，勿并发混用。

## 本次配置演练边界

配置开始先将生产开关设false，不创建production、accept或verification标签，不连接服务器或生产数据库/Redis，不读取应用.env，不合并真实main。只创建rehearsal标签：无生产secrets、临时bare Git、合成文件、fake SSH；它自动执行，无需GitHub点批准。测试覆盖无授权拒绝、请求者与rerun身份、tag策略、A验收后B自动整合、冲突保留和解决后重测、检查失败不创建请求、失败验收不删任务、成功精确验收才关闭，以及既有服务器锁/版本保护。GitHub实跑核验无pending_deployments审批等待和多请求串行。全部通过后恢复生产入口可用，但不创建生产请求。

底层SSH网关、远程部署脚本、迁移与生产数据库身份守卫本次保持不变。凭据验证证据可复用，不能把本次无数据演练说成真实部署或迁移验证。真正未来部署才运行release.sh --deploy-task SHA --reviewed-sha SHA --skip-test，复用精确GitHub检查，保留构建、身份、迁移及readiness校验。

GitHub queue:max由实际平台运行验证；旧actionlint不识别该新增字段时只对临时副本去掉该行检查其他语法，真实文件不得移除队列。

## 专用SSH发布入口与无数据验证

既有执行器以root安装后端依赖、执行应用代码、准备挂载并管理服务。本次不重构生产服务身份；专用密钥仍具有**高权限应用发布能力**，不能声称它是无法读生产数据的低权限账号。安全边界是Codex中的用户明确指令、可信仓库写入身份、精确检查候选，以及服务器SSH命令限制。不要把该私钥交给Codex开发环境、普通job、个人日常SSH或第三方任务。

全新ed25519密钥使用root的authorized_keys独立条目，带`restrict,command="/usr/local/sbin/everydayai-ci-ssh"`。保留原管理密钥；不修改sshd配置、不重启应用服务。网关由root拥有，位于应用同步目录之外；策略为root-only的`/etc/everydayai-ci/policy.json`，使用已存在的python3.11。禁止PTY、交互shell、转发、scp和任意命令。rsync仅接收固定frontend/backend/deploy路径、固定选项，不允许sender、可疑路径或目录symlink，并在receiver保护.env*、config.env、venv不被删除。接收端过滤不能阻止恶意发送方覆盖这些路径；正常发布仍使用deploy.sh现有发送端exclude规则。此次只在全新临时目录用合成文件核验此边界，没有对生产配置做写入试验。发布stdin必须与管理员审查安装的源码hash吻合，argv还须通过操作、锁令牌、迁移路径等校验。应用代码本身仍是高信任内容，命令限制不构成对恶意已授权应用代码的沙箱。

`build_ssh_policy.py`仅提取脚本并计算hash，不执行它们。策略与网关不会由发布job自动更新；以后受控远程脚本/网关有变化，必须先经管理连接审查更新，否则正式执行会失败关闭。普通业务改动可沿用相同脚本策略。正式执行还会先比对服务器网关hash、脚本策略映射和来源祖先，防止错装旧网关或不同策略。

`Verify release credentials`工作流由新`delivery-verification/<SHA>/<nonce>`标签显式触发，使用同一production请求标签限制环境，但只运行`verify_delivery.py`固定探测，**绝不调用release.sh**。它检查专用SSH及严格known_hosts、root身份、固定路径存在性、五个服务是否active、发布标记和锁、网关/策略hash，并验证任意`id`命令被拒绝。来源checkout包含完整历史。主机公钥通过原有严格校验的管理SSH通道核验，不盲信ssh-keyscan。

本轮验证不读取应用.env、不连接PostgreSQL/Redis、不查询生产业务、不执行迁移、pip安装、文件同步、重启或健康接口。不能因此证明真实部署及迁移成功；首次正式授权发布仍会运行既有数据库身份、迁移和readiness守卫。production标签与验收标签不会在凭据验证中创建。

撤销该身份时，用原管理连接删除authorized_keys中独立的`everydayai-github-delivery-20261018`条目，并删除GitHub三个专用secret、将生产开关设为false；不删除其他管理密钥或应用文件。授权前备份仅用于对照，撤销时不直接覆盖整份authorized_keys，避免抹掉后续合法修改。

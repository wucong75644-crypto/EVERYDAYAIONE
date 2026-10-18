# GitHub审批发布与隔离演练

本次交付是配置和演练：生产开关`PRODUCTION_DELIVERY_ENABLED=false`，`production`环境没有发布凭据。不连接生产服务器、数据库、Redis或备份，不执行生产部署，不合并main。不能把演练成功说成生产连通性或正式发布成功。

## 确定提交、审批和排队

日常在Codex任务内测试和预览。准备发布时先合入最新`origin/main`，解决冲突，通过受控入口提交推送，然后请求`Pre-release checks`。前后端两个job必须对同一完整SHA成功；取消、跳过、旧提交通过都不算。

候选提交必须包含本次流水线配置。以实际通过的检查run ID请求：

```bash
# 默认只有隔离演练；可以先--dry-run核验，不创建请求标签。
python3 scripts/ci/delivery.py request --mode rehearsal --check-run <run-id>
# 以后正式启用且明确授权发布时：
python3 scripts/ci/delivery.py request --mode production --check-run <run-id>
```

命令只创建指向已推送任务SHA的新`delivery-*`标签，不修改任务分支或强推。标签包含模式、完整SHA、检查run、任务名和随机请求ID，工作流源代码必须就是该SHA。无需先把本次配置合入main才能演练。普通push、检查成功本身不部署；显式请求之后还必须在GitHub **Review deployments** 审批对应环境/完整SHA。

发布/演练共享仓库级`everydayaione-production-delivery`队列，验收使用独立`everydayaione-production-acceptance`队列，避免B等待发布审批时把A的验收堵在后面。两者都为`queue: max`、`cancel-in-progress: false`；真正服务器写入仍必须取得同一服务器锁，不能并发。每个队列按进入时间FIFO，最多100个等待请求，并非按用户点击顺序作分布式绝对排序。审批等待会占住该工作流队列位置，后续同类请求不能挤掉它。队满、拒绝、超时或人工取消会产生未成功结果，不能当成已发布。若两个队列碰到共享服务器锁，锁忙任务明确失败保留现场，待持有者完成后重新请求，不抢锁或盲目重跑不确定写入。

GitHub生产环境要求仓库所有者审批、禁止管理员绕过，只有特定`delivery-production/*/*/*/*`和`delivery-accept/*/*/*/*`标签可进入。当前是个人仓库，同一所有者可发起并审批自己的请求（prevent_self_review=false），否则唯一操作者无法审批；没有自动批准生产的代码。自动化若持有所有者的广泛API令牌，同样拥有该身份权限，因此Codex开发环境不应配置该广泛令牌/生产秘密。

审批后重新核验检查run、任务远端tip和最新main；不在审批后自动合并或更换候选。runner创建临时独立任务工作树，以`release.sh --deploy-task SHA --reviewed-sha SHA --skip-test`调用原受控入口。`--skip-test`仅复用已核验的GitHub检查，既有构建、生产数据库身份核验、迁移校验、健康检查和服务器锁仍保留；真正生产连接只在以后明确启用并审批的正式运行中发生。

## 防止任务相互覆盖

服务器共享锁取得后再次核验：

- HEAD和远端任务tip必须仍为审批SHA，包含最新main。
- 当前生产完整版本标记必须已知、可在仓库历史中验证，并且是候选的祖先。
- 当前生产若尚未合入main，仅同一个来源任务可继续修复迭代；其他任务即使手工包含了该提交，也不能越过待验收状态。
- 未知/损坏/部分失败的标记、锁归属不确定、代码落后均拒绝，不自动删锁、覆盖或伪造初始生产版本。

因此A发布待验收时，B可以并行开发和测试，但B的发布会被阻断。先验收A，再让B在自己的工作树同步最新main、重新检查和审批。队列只协调工作流执行，不替用户验收，也不会为B自动解决代码冲突。旧版发布脚本、人工SSH和绕过此入口的写入不在这套自动保证范围；正式启用后不要混用旧发布入口。

生产测试通过后，用户明确说“清理工作树/验收关闭”才创建独立验收请求：

```bash
python3 scripts/ci/delivery.py request --mode accept --check-run <该生产SHA的检查run-id>
```

它仍需单独审批、独立验收队列及同一服务器锁，调用`release.sh --accept-and-close`。核对生产标记、最终main代码树与已测试候选一致后才推main；不重复部署。仅验收job有`contents:write`。清理的是runner临时工作树；原Codex VM中的任务、修改和数据不会被跨机器自动删除，待原对话确认远端验收结果后安全关闭。稳定基座Git元数据也不会跨VM实时同步，新任务/后续发布须fetch最新main。

## 演练与生产启用边界

演练用独立`delivery-rehearsal`审批环境，不持有生产secrets。审批后运行临时bare Git仓库、临时模拟服务器目录和fake SSH的真实受控入口测试，包括当前版本保护、待验收任务保护、main变化、并行锁、远端写入不确定保留锁及验收后下一任务集成。实际GitHub演练还核验审批等待和多请求排队，不能只靠本地测试宣称平台门禁有效。

本次生产开关保持false，三个生产环境secret保持未配置：

- `DELIVERY_CONFIG_JSON`：现有受控执行器的服务器、目录、域名等发布元数据JSON；禁止数据库URL、Redis密码、应用.env或API密钥。
- `DELIVERY_SSH_KEY`：后续单独授权接入的专用发布私钥，不读取/上传当前电脑已有私钥。
- `DELIVERY_KNOWN_HOSTS`：经过独立核验的服务器主机公钥，严格主机校验；不用`ssh-keyscan`盲信首次连接。

以后真正启用前，需单独完成凭据和最小服务器权限接入、网络可达性、当前生产版本标记可追溯性和首次候选集成检查，再由用户授权把开关改为true。若当前生产版本标记缺失，必须独立调查和人工确认，不能让流水线猜测。验收job还需确认仓库对GitHub Actions推送main的权限/分支保护允许；本轮不推main验证该权限。

部署JSON只接受这些字符串字段：SERVER_HOST、SERVER_USER、SERVER_PORT、REMOTE_APP_DIR、REMOTE_FRONTEND_DIR、REMOTE_BACKEND_DIR、DOMAIN、EMAIL、BACKEND_PORT、FRONTEND_PORT、RUN_MIGRATIONS。既有执行器含固定服务路径，因此当前仅接受`/var/www/everydayai`及其`frontend`/`backend`子目录；不假装支持任意迁移部署根目录。它在runner上以0600文件生成，SSH凭据仅在审批后的临时目录存在，不进入Codex开发环境、Git或artifact。正式执行仍沿用既有生产schema迁移规则；开发bootstrap绝不作为生产初始化。

## 验证及工具限制

使用`test_delivery.py`和`test_release_coordination.py`做隔离演练，`test_ci_checks.py`验证只读检查。GitHub正式解析并运行`queue:max`作为平台支持证据。当前actionlint 1.7.12尚未识别官方新增的queue字段：对其余原文去掉唯一这一行进行兼容语法检查，测试另行断言该字段必须为max；不通过删除真实工作流队列配置来迁就旧校验器。来源：[GitHub队列官方文档](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)。

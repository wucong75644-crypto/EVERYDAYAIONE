# Agent Reach 本地验证与上线准备

当前为基础集成，未通过真实组织账号验收。豆包网页搜索继续使用原有配置。

## 独立运行环境

在需要运行服务的机器执行 `scripts/setup-agent-reach.sh /absolute/path/to/new/runtime`。脚本拒绝覆盖已有目录，将 Agent Reach 及具体 CLI 安装到独立 venv；固定上游提交和已验证依赖版本。不要运行上游自动安装来更改全局环境。Node需提前安装；脚本将既有Node链接到该独立环境，字幕调用显式使用该路径。依赖清单位于backend/requirements-agent-reach*.txt；freeze来自本次macOS环境，生产Linux仍需安装验收。

服务器设置：AGENT_REACH_BIN_DIR指向环境bin目录；总开关AGENT_REACH_ENABLED；查询渠道AGENT_REACH_PLATFORMS逗号分隔。默认总开关关闭，查询渠道web,github,rss。Exa需要AGENT_REACH_EXA_API_KEY；YouTube评论读取使用获授权连接或AGENT_REACH_YOUTUBE_API_KEY，API key放请求头，避免URL日志泄露。组织凭据使用现有CONFIG_KEK_CURRENT_VERSION/CONFIG_KEK_KEYRING_JSON信封加密，不生成或打印生产密钥。

AGENT_REACH_WRITE_ACTIONS为显式允许的platform:action列表，如twitter:set_like；默认空。逐项通过真实账号验收再放行，无管理员逐条审核。

小红书需锁定xpzouying/xiaohongshu-mcp提交a5c8f7799980ba1fdd501999843eb2d17e4c9a9f，每个连接一个独立REST实例、独立cookies目录、认证Token，绑定本机回环地址。AGENT_REACH_XHS_ORIGINS_JSON为连接UUID到http://127.0.0.1:端口的映射；同实例不能分给不同连接。仅使用REST，不向模型暴露其MCP或任意接口。写入前通过login/status核对user_id，旧版本无该字段会拒绝使用。组织账号ID需填写真实平台ID（YouTube为频道ID，Reddit为me.id，小红书为个人主页ID），不要填写昵称。

## 组织账号

设置→组织管理→互联网账号：管理员连接组织账号、授予指定成员查询/写权限。小红书/B站扫码，YouTube官方授权，Twitter/X和Reddit会话导入；页面不要求JSON或账号ID。保存前验证账号身份，凭据加密且不回显。YouTube支持refresh_token刷新；账号失效使用重新连接，必须匹配原账号并保留成员授权。断开清除密文并使连接不可用。

API前缀/api/agent-reach：GET connections、POST connections、PUT connections/{id}/grants、DELETE connections/{id}；GET operations只返回当前操作者最近50条回执。组织身份来自服务端OrgCtx，不接收请求中的org_id覆盖。数据库迁移291、292必须按顺序在启用前完成，生产迁移只走项目既有受控发布流程。

AI使用list_connections取得获授权连接ID，查询时每次一个明确动作；发帖和评论在现有确认界面由操作者确认最终内容。点赞使用liked目标状态。所有写入走既有公共工具执行入口，不能直接调用handler绕过权限。出现uncertain/executing先人工核对目标账号页面，不创建新调用重发。

账号锁协调同一主机的多个进程。当前部署拓扑只支持单主机；扩到多实例前必须替换为分布式锁并验收。撤销阻止下一次派发，已发送到平台的请求无法撤回。每条写入仅派发一次，并保存回执；这不等于跨不同调用身份的永久内容去重。

## 可重复本地测试

Python执行backend/tests/test_agent_reach.py和test_agent_reach_media.py，连同test_tool_policy.py、test_web_search_provider.py、test_agent_tools.py、test_tool_executor.py。测试环境需提供虚拟DATABASE_URL与JWT_SECRET_KEY，不使用生产env。前端运行tsc及ReachConnections.test.tsx。

PostgreSQL专用测试：在全新临时cluster中，用bootstrap.sql建立最小组织夹具，再分别以psql -1依次应用291和292迁移，再执行rls.sql。夹具路径backend/tests/fixtures/agent_reach。仅在该一次性实例设置REACH_TEST_DSN='host=/private/tmp port=55479 dbname=postgres'运行test_agent_reach_postgres.py；测试强制检查本机socket和端口，绝不读取DATABASE_URL。结束后停止该临时cluster。夹具覆盖新表RLS，不代表完整历史迁移链已验证。

真实验收需逐项验证：登录有效/到期/账号不符、搜索/完整正文/无字幕/限流、并发账号隔离、操作者确认、一次写入回执、取消与超时的不确定状态、成员权限撤销。真实发布使用组织指定测试账号和明确测试内容，须获得用户对应操作授权。

已接入小红书图文/视频发布及YouTube/B站视频上传；GitHub只接公开仓库，私有仓库不纳入。媒体需当前授权工作区的签名文件引用，禁止传服务器路径或素材URL。图片单张20MB，MP4视频单个100MB，合计120MB。发布须明确可见性；B站须封面、分区、标签和原创声明，转载须来源URL；YouTube须分类。

AGENT_REACH_MEDIA_STAGING_DIR默认/tmp/everydayai-reach-media，目录由服务账号独占；小红书REST实例需共享同一绝对路径及服务账号，否则上传不能读取素材。写动作放行项为xiaohongshu:create_post及各平台:upload_video。上传受60至180秒总预算限制，无自动续传或重发。回执submitted/uploaded/backend_submitted仅表示提交或上传，发布与审核状态需在平台核验。

## 交互登录配置（2026-10-11）

YouTube需配置AGENT_REACH_GOOGLE_CLIENT_ID、AGENT_REACH_GOOGLE_CLIENT_SECRET、AGENT_REACH_GOOGLE_REDIRECT_URI；官方应用类型Web应用，回调精确为https://实际域名/api/agent-reach/oauth/callback。启用YouTube Data API，配置OAuth同意屏幕、所需范围youtube.force-ssl与youtube.upload，以及测试用户/审核要求；仅部署代码不代表OAuth应用可面向所有用户使用。Client Secret通过既有服务环境管理，不写入源码或聊天。授权拒绝、无频道、权限不全均不能保存为已连接。反向代理对该回调日志使用路径而非完整查询URL，禁止记录授权码、state、凭据请求体。

小红书AGENT_REACH_XHS_SLOTS_JSON结构为组织UUID映射到槽位数组，每个槽位含connection_id（预生成UUID）、origin（独立本机回环REST端口）、service_token（服务认证令牌）。值只在服务端环境配置，前端不返回。运维为组织预分配空登录服务、独立cookies目录和共享素材挂载；新服务残留登录会拒绝收编，需运维清理。槽位不能和旧AGENT_REACH_XHS_ORIGINS_JSON重复；旧已连接账号仍可检查/重连。一个槽位绑定一个连接；撤销后需分配新UUID，不回收给其他组织。扫码请求失败或取消后槽位锁保留到过期，避免复用尚未结束的扫描。取消扫码只停止应用接入，上游仍可能保存Cookie，下一次操作前必须由运维检查/清理。

Redis不可用时登录流程拒绝继续，不退回内存态。二维码3/4分钟过期，OAuth10分钟过期。刷新密钥保存加密信封；访问令牌刷新当前为每次过期调用临时使用，不自动修改成员权限。小红书/B站扫码与YouTube授权尚需真实账号验证。Twitter/X和Reddit目前仍需管理员手工导入Cookie；不支持无手工操作的一键登录，也不自动读取本机浏览器。

本轮本地验证：795项后端定向测试（含一次性PostgreSQL集成）和6项前端交互测试通过；前端类型检查、定向ESLint通过。独立临时环境的固定QR桥接产出有效PNG。测试数据库请使用UTF8编码；不连接生产实例。未执行真实扫码、官方授权或发布，未部署。

## 服务器配置检查与安装入口

现有受控发布会同步backend/和deploy/，不会同步顶层scripts/。服务器安装入口为deploy/setup-agent-reach.sh，本地scripts/setup-agent-reach.sh仅委托该入口。脚本默认使用python3.11，其他已安装的3.11+解释器通过AGENT_REACH_PYTHON_BIN指定；运行环境必须是全新绝对路径，不覆盖旧版本。Node通过AGENT_REACH_NODE_BIN指定或使用已有可执行文件，不自动修改全局安装。

配置参考deploy/agent-reach.env.example；仅合并所需字段，不直接覆盖现有.env，也不由发布脚本自动生成密钥。API和Conversation Actor使用同一backend/.env。保持总开关与写操作关闭，直到逐平台验收。

从backend目录运行 `venv/bin/python -m services.agent.agent_reach.preflight --offline --platforms web,github,rss,youtube,bilibili,twitter,reddit,xiaohongshu,exa`。它只读取本地配置和隔离环境的包元数据，检查固定版本、Git提交、OAuth字段、服务映射、目录权限和加密配置，不打印配置值、不连接平台、不读取浏览器、不修改应用配置或数据、不执行登录。去掉--offline才额外只读检查数据库结构和Redis可达性；不执行迁移。输出configuration_ready不等于真实账号验收通过，platform_acceptance始终为not_verified。退出码1表示存在缺项。

首次Google Cloud配置见[YouTube官方授权步骤](agent-reach-google-oauth.md)。当前项目配置的网站域名为everydayai.com.cn，独立测试域名必须同步控制台回调与服务环境。申请云应用、填写凭据、修改生产.env和重启生产服务不由本地检查自动完成。

## 2026-10-11 服务器接入准备进度

Google Cloud OAuth测试应用、两项YouTube范围、Web客户端和已确认的测试账号已配置。凭据通过下载文件核验后写入服务器现有环境文件，原配置已保存在服务端受限备份目录；未输出密钥，未重启服务。独立运行环境安装于`/opt/everydayai-agent-reach-20261011`，55项固定依赖版本/源码提交元数据比对通过。上述操作不等于应用代码已部署；Agent Reach渠道开关未自动开启。小红书服务器尚无专用浏览器/扫码服务，需要先确定所属组织，再配置独立服务、凭据目录和素材路径。真实账号连接及写入验收未执行。

## 全部现有组织的小红书服务准备

2026-10-11：按已确认范围，为当时全部2个有效组织各创建1个独立服务。服务使用固定上游源码a5c8f7799980ba1fdd501999843eb2d17e4c9a9f，二进制位于`/opt/everydayai-xhs-a5c8f779/xiaohongshu-mcp`，构建使用官方Go1.27.2归档并核对官方SHA256；Linux二进制SHA256为69e913e13631cc2f98cdfc369cef2f433c0e82ff1fdba0ea254b63a4a1a57066。配套Chromium148.0.7778.215归档SHA256为a0a50ba07be7db22bd74dac79c23a9fc1f3b8cbf8a647cc2e04465ae176cdb77，已校验、检查动态依赖及版本。

两个systemd服务已启动，仅监听127.0.0.1:18061/18062；健康检查通过，未认证API请求返回401。每个连接使用独立工作目录、Cookie路径、HOME、认证令牌和UUID；媒体目录由现有root服务账号独占，共享浏览器缓存不包含登录状态。单实例MemoryMax=1G，浏览器由请求按需启动。此证据不代表真实扫码或平台搜索/发布验收已通过。

使用`deploy/provision-agent-reach-xhs.py`可为明确的组织UUID补充服务：通过后端venv执行，重复传`--org UUID`，提供`--binary`和`--cache`的上述绝对路径；默认仅输出计划，`--apply`才创建专用目录、令牌、systemd服务并合并现有环境文件。脚本不连接小红书、不扫码、不重启API/Actor、不启用总开关；保留环境备份。重复计划已确认existing=2、to_provision=0。脚本失败后检查新建服务现场，不自动删除Cookie或覆盖已有账号。

“全部组织”本次覆盖当前全部有效组织。以后新建组织仍需运维执行该配置入口分配专用服务；尚未实现组织创建时自动配置服务。应用代码尚未发布，管理员入口需代码部署及渠道启用后才能使用。发布准备发现主分支已占用旧迁移编号，任务未发布迁移已调整为291和292；原有本地RLS验证对应SQL内容不变。

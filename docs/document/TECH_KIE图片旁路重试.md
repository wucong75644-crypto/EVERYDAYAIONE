# KIE 图片临时空间单次重试

## 边界

只接管 KIE 图片结果 failCode=400 及已进入专用重试的任务。首次提交、7890/7891 路由、两条旁路上传出口、正常成功持久化和前端协议不变；不增加上传或模型兜底。保留已有启用变量，不新增配置。

## 请求与上传

海外旁路按原 KIE task_id 缓存 pending/ready/failed、实际 model/input、源 URL 与临时 URL 映射，一小时过期。实际请求快照不含认证头和回调地址。重试只替换图片 URL，保留顺序、重复项及逐张批次参数；旧缓存缺少快照时复用首次生成参数整理函数，不能还原则结束。

两条上传旁路共用工作区原文件读取：仅将配置内 CDN/OSS 的 `workspace/` 原图 URL 精确映射到当前可信身份的工作区文件，不搜索同名文件，不扫描对话历史。移除旁路 CDN 下载和下载备用；缺文件、读取失败、非原图链接或上传失败时记录旁路失败，只有完整海外链接才能用于重试。读取在线程中执行，保留 60 秒等待与 30MB 上限，校验路径越界和符号链接。原旁路上传继续继承环境代理，海外上传继续使用 `KIE_SHADOW_OVERSEAS_PROXY`（7891）；主任务仍使用原图 URL，正常 CDN 保存不变。现有顺序、去重上传及按原请求逐项重放机制保持不变；成功日志增加 `source=workspace`，读取失败记为 `stage=workspace_read`。

`create_image_adapter` 的 `shadow_user_id/shadow_org_id` 仅向 KIE 客户端透传后端身份，不进入生成请求。图片 Handler、普通工具、电商 Agent 和已有换模型调用处传入当前用户/任务的组织身份；工具及电商入口沿用已有 `workspace_user_id`（包括可信企微频道作用域）。读取复用 `resolve_asset_identity` 检查存储归属，再以 `FileExecutor.extract_user_relative_path` 检查真实路径，缺身份或越权时旁路失败，不影响首次生成。身份不从 URL、提示词或生成参数中推断。

读取时忽略 fragment；query 仅放行 `v/t/ts/_` 缓存标记及 `OSSAccessKeyId/Expires/Signature/security-token` 鉴权参数，后者依据 [OSS V1 签名说明](https://www.alibabacloud.com/help/en/oss/developer-reference/ddd-signatures-to-urls)。URL 签名不作为工作区授权凭证，授权仍由后端身份决定。`x-oss-process`、`versionId` 及未知参数继续拒绝，防止把处理图或历史版本误换成当前原图。保存与重放仍使用完整原 URL 作为映射键，不丢失顺序和重复位置。

## 状态与一致性

独立 KieImageFallbackService 在 TaskCompletionService 失败结算前运行。request_params 内记录 waiting/submitting/submitted/failed、原任务号及等待截止时间；不迁移数据库。等待上传窗口 60 秒。现有回调模式默认 120 秒轮询，故模块内仅短暂轮询上传缓存；就绪、失败或到期后通知原完成入口一次，随后退出。进程重启后原轮询可续接 waiting 阶段，不改变全局频率。

沿用完成处理锁和数据库 version 条件更新，提交前持久化 attempted/submitting 标记。经正常 `7890 → api.kie.ai → overseas-ss` 提交一次，限制 60 秒；不再次启动上传旁路。成功取得新任务号后更新同一本地任务的 external_task_id。

请求超时、非 JSON/缺少任务号响应或 5xx 不盲重发，进入原最终失败处理。如果 KIE 已受理而任务号绑定失败，日志记录本地、原 KIE、新 KIE 任务号及 manual_review；不增加自动补绑定。数据库持续不可用时保留已落库的 submitting 标记，待原完成入口可继续处理时终结，不再提交。

明确取舍：上述跨系统异常需要人工核查，可能无法自动取回 KIE 已生成的结果；不保证外部受理与本地写库的原子性。删除本轮尚未发布的回执恢复缓存、内存回执、180 秒恢复循环及跨进程文件锁，不保留它们的局部残余。

复用本地任务、占位符及原积分锁定；最终成功确认一次，失败退款一次。等待与重提交期间不发失败事件。新生成重新计时。已绑定外部任务号的 KIE 图片超时只走统一完成处理，不允许旧任务快照直接标失败或退款；锁内重新核对阶段与时间。未绑定任务号的首次提交保留既有超时清理。专用重试最终失败不能再进入智能换模型。启动孤儿清理保留专用重试，由原完成入口结算，其他任务行为不变。

## 修改与移除

- 独立 service/request 两个模块：资格、参数重放、短暂等待、单次提交与超时判定。
- task_completion_service：失败分流、锁内超时保护。
- async_retry_service：移除迁出的 KIE 特例，保留 smart retry。
- KIE client/adapter：首次调用保持原行为；提供单次、不启动旁路的内部重放入口。
- shadow_upload_store：沿用一小时上传缓存，增加状态和请求快照；无新提交恢复缓存。
- background_task_worker：KIE 图片不使用旧快照直接超时结算。
- task_recovery：启动恢复保留专用重试，不提前退款。

移除前等待在途重试结束；删除两个独立模块以及完成处理、后台轮询/超时、启动清理中的对应调用、缓存扩展、内部重放入口。两条上传旁路及代理配置可独立保留。回滚至旧程序也须先排空在途重试，不能仅删除单个模块文件。

## 验证

真实适配器配合模拟 HTTP/Redis/数据库，覆盖实际参数保持、旧缓存兼容、晚到与部分失败、多图顺序、重复通知、提交结果不确定不重发、绑定失败日志、旧超时竞争、积分不重复处理、前端生成状态及无重复上传。另检查受影响的 KIE、重试、任务完成、批次和后台清理测试；不调用生产生成接口。

2026-09-08 精简后本地验证：上述 12 个测试文件共 185 项全部通过，`git diff --check` 通过。日志：`/tmp/kie-fallback-lean-final-20260908.log`。使用隔离测试配置和模拟外部接口，未执行真实付费生成、提交或生产部署。

2026-09-08 工作区取图修改：KIE 旁路、请求重试、缓存、JSON 客户端和媒体 URL 共 5 个测试文件 96 项通过。覆盖两条出口不变、三种图片输入字段、同名不同文件、混合来源顺序、重复位置、中间读取/上传失败不缓存残缺映射、无 CDN 下载、路径安全与大小限制；`git diff --check` 通过。日志：`/tmp/kie-workspace-tests-20260908.log`、`/tmp/kie-workspace-regression-20260908.log`。仅本地模拟验证，尚未提交部署。

后续边界修复：补齐缺身份、跨用户/组织、个人和频道工作区、路径规范化边界、安全 query/fragment、处理/版本参数拒绝与工厂身份透传测试，并回归图片 Handler、换模型、工具、电商及工厂入口。10 个文件 248 项通过（首轮 247 项通过，1 项新增测试的模拟响应漏填 `msg`，补齐测试数据后定向复验通过）。日志：`/tmp/kie-workspace-owner-tests-20260908.log`、`/tmp/kie-workspace-owner-recheck-20260908.log`。独立静态安全复查未发现新的高置信问题；未访问生产，尚未提交部署。

## 发布入口兼容同步

2026-09-08 经用户确认，仅从本地 `prompt-framework-coherence` 任务同步 `deploy/release.sh`、`deploy/deploy.sh`、`deploy/release-coordination.sh` 及两个发布测试文件，补齐发布协议 v2 的生产锁、候选失效/重建和验收一致性保护。未同步其他任务的规则、业务代码或工作树取消功能，未修改来源工作树。发布协调 10 项测试、发布验收生命周期测试和 Shell 语法检查通过，均使用临时本地仓库和模拟 SSH，不访问生产。

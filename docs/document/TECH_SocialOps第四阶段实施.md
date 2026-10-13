# 社媒运营第四阶段实施记录

基线：origin/main `8266a192c52fd2ef03e6db5e42433eaf17fde626`。任务：`codex/task/20261012233940-social-ops-phase4`。仅本地实现，未提交部署。

## 事实与方案差异

前三阶段已实际提供不可变草稿版本/素材、整包检查、立即发布意图、公共工具操作者确认和操作回执，以及社媒后台宿主。第三阶段提交只有backend_submitted，不带可靠平台ID；本阶段仍保留人工核验。总体方案中的实体/API是建议，实际复用social_publish_intents而不建设第二张业务队列表。授权与租约增量加在同一意图，social_publish_events保存历史摘要。

普通scheduled工具策略明确禁止dangerous，技能快照不能授权写入。本阶段增加服务端对象形式的精确授权入口，保持permission_mode=ask、execution_mode=scheduled，不制作ToolConfirmation。Policy纯比较；身份刷新和handler边界负责IO核验。它只允许对应原意图的xiaohongshu:create_post，其余危险后台操作仍拒绝。

原BackgroundTaskWorker在回调模式默认120秒轮询，不能满足60秒默认窗口。因此同宿主新增15秒有界扫描协程，不新增基础设施，不改变其他任务的轮询/模型费用。确认窗口默认60秒，操作者可显式设置最多3600秒；停机超过窗口默认过期，不补发。

图文实际渲染和Skill沿用第三阶段，不引入Easel JSON排期脚本、不新增Skill和模型调用。立即发布仍经原交互确认；定时不确定记录不能借原“人工核验未发布”入口重发。

## 状态与原子边界

awaiting_confirmation → scheduled → preparing → dispatching → submitted → verified。

预算不足在preparing之前或之中返回waiting_budget，未创建平台操作、未消费授权；到窗口再领取相同ID。内容/账号/权限变化为invalidated/paused，撤销旧授权；取消为cancelled；窗口外为expired。所有后续发送均须人工重新检查确认。

preparing具有租约，可在未消费、未过期时回收。账号锁、媒体快照、实际参数、成员权限及原版本再次核验之后，条件更新同一行到dispatching并消费授权，才保留操作ID并调用原公共派发。发送附近异常不自动重试。dispatching超过租约为uncertain，旧Worker结果不能覆盖；已有工具回执可对账为submitted，不等于公开核验。

内容版本和连接更新触发器原子撤销尚未消费授权。多Worker和取消竞争通过行更新谓词/lease_token/grant_hash/grant_consumed_at保护。实际平台请求与数据库不可能跨系统原子；不承诺平台恰好一次。

## 实施批次与验证

1. 迁移298：授权/排期/租约、索引、RLS、审计摘要和宿主受限函数。
2. 发布服务及专用Worker：准备、批准、撤销、领取、消费、预算等待、回执恢复，沿用ToolExecutor和Agent Reach。
3. 工作台：完整确认选择定时、时区窗口、列表/日历、修改/取消、状态详情及核验引导。
4. 隔离数据库并发/故障/RLS/API测试、公共工具策略与立即发布回归、前端交互、类型、构建和迁移验证。

具体操作与限制见`docs/runbooks/social-ops.md`第四阶段。真实平台验收须单独授权，当前没有执行。

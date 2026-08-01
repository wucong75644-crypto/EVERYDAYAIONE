# Tool Confirmation V3

## 安全边界

旧 ToolLoop 不再把通知、`None` 或异常解释为授权。显式 SAFE 工具直接执行；CONFIRM
与 DANGEROUS 工具必须完成 Redis challenge，并由当前调用栈取得唯一
`EXECUTION_CLAIMED` 后才能调用 Handler。未知工具、三表不一致、Redis 错误、缺少
task、发送失败、超时和协议错误均失败关闭。

## Redis 合同

三个 key 使用同一 `{tool-confirm}` hash slot：

- `ws:tool-confirm:{tool-confirm}:identity:<identity_sha256>`：execution identity 唯一索引；
- `ws:tool-confirm:{tool-confirm}:challenge:<random_confirmation_id>`：不可变绑定与状态；
- `ws:tool-confirm:{tool-confirm}:signal:<random_confirmation_id>`：可丢失唤醒 List。

Identity 为 task、tool call、规范工具名、用户及组织。Hash 另存确定性 JSON 参数摘要。
原始参数、waiter token、Secret 和业务正文均不得进入 Redis。create 使用 Lua 原子创建，
相同 confirmation、binding 与 waiter hash 的重试严格幂等；其他并发创建冲突。状态为
`PENDING → APPROVED | DENIED | EXPIRED`，只有原 waiter token hash 可执行
`APPROVED → EXECUTION_CLAIMED`。确认窗口 60 秒、claim window 15 秒、终态保留 120 秒。
所有截止时间由 Redis `TIME` 判定。

## 协议与展示

服务端请求 payload 为 `protocol_version=3`、随机 `confirmation_id`、规范工具名、
安全等级、timeout 和字段 allowlist 生成的 `confirmation_summary`。不发送 arguments、
路径、文件名、OSS key、代码或完整 prompt。客户端仅回传
`{confirmation_id, approved}`；旧 `{tool_call_id, approved}` 返回
`TOOL_CONFIRM_PROTOCOL_OBSOLETE`，不能消费 challenge。

## Handler / Safety / Preview 对照

下表是批次 0 冻结审计证据；集合测试保证 `ToolExecutor._handlers` 是 Safety Registry
子集，且全部非 SAFE 项与 Preview Registry 精确相等。

| Handler（逐项列名） | 分类 | 已审计副作用 | Preview |
|---|---|---|---|
| `get_conversation_context`、`search_knowledge`、`erp_api_search` | SAFE | 本地/组织内只读 | 不创建 |
| `evidence_search`、`evidence_get`、`artifact_search`、`artifact_get`、`artifact_read` | SAFE | 证据与产物只读 | 不创建 |
| `memory_search`、`memory_get`、`file_search` | SAFE | 记忆或 Workspace 元数据只读 | 不创建 |
| `erp_info_query`、`erp_product_query`、`erp_trade_query`、`erp_aftersales_query`、`erp_warehouse_query`、`erp_purchase_query`、`erp_taobao_query` | SAFE | 已配置 ERP 固定只读 operation | 不创建 |
| `local_data`、`local_product_identify`、`local_stock_query`、`local_product_stats`、`local_platform_map_query`、`local_compare_stats`、`local_shop_list`、`local_warehouse_list`、`local_supplier_list` | SAFE | ERP 本地只读 | 不创建 |
| `social_crawler` | SAFE | 固定公开网络只读 | 不创建 |
| `file_analyze` | CONFIRM | 派生 staging 写入 | 固定通用说明 |
| `fetch_all_pages` | CONFIRM | ERP 分页读取与 staging 写入 | 记录类型枚举 |
| `erp_agent`、`erp_analyze` | CONFIRM | 已限制为分析/只读部门能力；写 action 在 DAG 门禁拒绝 | 固定“执行ERP分析” |
| `web_search` | CONFIRM | 外部搜索与费用 | 查询/站点类别枚举 |
| `generate_image`、`generate_video`、`image_agent` | CONFIRM | 外部模型费用、媒体生成和 Workspace 持久化 | 模型/尺寸枚举与数量 |
| `code_execute` | DANGEROUS | 代码执行与 Workspace 副作用 | runtime、代码长度、timeout |
| `erp_execute`、`trigger_erp_sync` | DANGEROUS | ERP 业务写或同步 | 操作类型枚举 |
| `file_delete`、`restore_file` | DANGEROUS | Workspace/OSS 删除或恢复 | 固定通用说明 |
| `manage_scheduled_task` | DANGEROUS | 计划任务持久写 | 操作类型枚举 |

新发现但不在表内的工具不补默认分类：Safety 查询和 Handler 执行层都直接拒绝，并在
后续产品分类批次登记。非用户路由控制工具不伪装成 SAFE。

## 运行与发布

List 只用于降低轮询延迟，Hash 是唯一授权事实；通知丢失时 waiter 继续轮询 Hash。
V3 通知还要求本 Worker 实际 WebSocket 发送成功，或其他 Worker 在实际发送后通过
短 TTL 随机 delivery key 返回 ACK；仅 Redis `PUBLISH` 成功不算送达。ACK key 不含
challenge、tool 或参数事实，且读取后精确删除。连接断开会对该连接已投递、仍为
`PENDING` 的 challenge 原子提交拒绝；任务取消也由 waiter 通过同一 consume 脚本拒绝。
任何 Redis 读取失败都不得使用本地批准缓存。进程崩溃、waiter 丢失或 claim 后崩溃均
不重新 claim、不重派工具。上线前必须验证生产 Redis 的 PING、TIME、EVAL、NX、TTL
与三 key 同 slot 原子脚本能力；本阶段不接 startup。发布时先发布前端 V3，再 drain
全部旧非 SAFE Owner，最后全量切换 V3 Backend，禁止新旧 Backend 混合执行非 SAFE。

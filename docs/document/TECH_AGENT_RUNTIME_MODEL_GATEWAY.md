# Agent Runtime Model Gateway 技术设计

## 1. 结论与范围

本设计唯一推荐新增独立的 `everydayai-agent-model-gateway.service`。Gateway 是现有统一配置与 Secret 控制面的受信任模型执行进程，不是第二套租户、模型或凭证系统。它通过 Unix Domain Socket（UDS）接收一次冻结的 ModelAttempt 请求，在自身进程内解析现有配置、解密 Secret、构造 Provider adapter 并完成一次 Provider 调用；Runtime Worker 永远不读取 KEK、明文 Secret 或 Provider API key。

不采用“在现有 Backend 内增加 UDS server”。`deploy/everydayai-backend.service` 运行公开 Web API，使用多 worker Uvicorn，并加载完整 Backend env；将模型 UDS 生命周期嵌入该进程会产生多 worker socket owner 竞争、扩大 KEK 与公网入口的共同故障域，也无法给予 Gateway 独立的 DB、文件和网络权限。独立进程可以复用同一代码与配置 SSOT，同时获得最小 Linux 身份、独立健康状态、drain 和故障隔离。

本批仅冻结设计，不实现生产代码、migration、测试或部署。以下边界保持不变：

- 无公共 HTTP API；仅本机 UDS。
- 不新增 credential storage，不改变组织/用户/平台配置及 fallback 语义。
- Runtime 仍拥有 Run、ModelStep/ModelAttempt、重试决策、`UNKNOWN` 和终态。
- Gateway 只拥有一次 Provider operation，不直接完成 Runtime ModelAttempt。
- ERP、Media、Scheduler、Sandbox 不进入本设计；Sandbox unit 与部署流程不触碰。
- production flags 与 `production_ready` 在 BG1～BG5 统一验收前保持 `false`。
- 不引入 Vault、消息队列、gRPC 或其他外部基础设施；协议使用 Python 标准库。

## 2. 当前代码事实

### 2.1 模型与配置调用链

当前 Runtime 模型路径为：

```text
RunAggregateSnapshot
  -> production_model.PostgresModelCallFactory.__call__
  -> get_agent_runtime_model_context_v2
  -> model_resolution.resolve_runtime_model
  -> ModelStepRequest
  -> infrastructure.model.adapter.ExistingProviderModelAdapter.complete
  -> runtime_adapter_factory.create_runtime_chat_adapter
  -> Provider adapter.stream_chat
  -> ResponseAccumulator / tool-call projection
```

精确代码位置：

- `backend/services/agent/runtime/production_model.py::PostgresModelCallFactory` 冻结模型、ContextReceipt、工具、revision、request hash 和一次模型调用参数；Worker 不应在 Gateway 返回后重新选择模型。
- `backend/services/agent/runtime/infrastructure/model/adapter.py::ExistingProviderModelAdapter` 当前同时承担 Provider 调用、流消费、错误分类、结果累积与 adapter 关闭。
- `backend/services/agent/runtime/infrastructure/model/runtime_adapter_factory.py::create_runtime_chat_adapter` 根据模型注册表和明文 API key 构造一次性 Provider adapter。
- `backend/services/agent/runtime/infrastructure/model/response.py::ResponseAccumulator` 负责流式文本、tool-call 与 usage 的确定性累积。
- `backend/services/agent/runtime/infrastructure/model/projection.py` 负责 request projection、provider kwargs、request hash 与 revision。

### 2.2 现有配置 SSOT 与缺失边界

`backend/migrations/223_agent_runtime_production_composition.sql::get_agent_runtime_ai_bundle` 已将 run、worker、execution token、租户及活跃 attempt 与现有 `_resolve_configuration_bundle` 绑定。它返回的 Secret 是 `secret_ref`，包含 encrypted payload、wrapped DEK、KEK version 和 payload version，不是明文。

`backend/services/configuration/bundles.py::AsyncSecretBundleResolver.agent_runtime_ai` 取得 bundle 后，仍必须通过 `AsyncSecretBundleResolver._decrypt_secret` 调用 `backend/services/configuration/material_service.py::SecretMaterialService`。后者依赖 `backend/services/configuration/envelope.py::KeyEncryptionProvider.unwrap_dek`；当前实现为 `LocalKEKProvider.from_environment`，读取 `CONFIG_KEK_CURRENT_VERSION` 与 `CONFIG_KEK_KEYRING_JSON`。

`deploy/everydayai-agent-runtime.service` 只加载 `/etc/everydayai/agent-runtime-worker.env`，并通过 `InaccessiblePaths` 禁止访问 Backend `.env`、`.env.kek` 和旧 `agent-runtime-model.env`。因此 Runtime 进程内构造 `SecretMaterialService` 不成立，也不应恢复旧 model env。

### 2.3 现有装配缺口

`backend/services/agent/runtime/production_factory.py::build_agent_runtime_production_components` 目前仍在真实安全服务缺失时失败关闭；没有 Gateway client、`SecretMaterialService` 或 production credential broker 的构造入口。`backend/services/agent/runtime/composition.py::build_runtime` 是 Runtime Worker 装配入口，BG4 只允许在这里接入 Gateway client，不允许恢复 global settings 或 Provider API key fallback。

## 3. 进程与信任边界

### 3.1 独立进程

新增：

```text
systemd unit: everydayai-agent-model-gateway.service
entrypoint:   backend/agent_model_gateway_main.py
call socket:  /run/everydayai-agent-model-gateway/gateway.sock
health socket:/run/everydayai-agent-model-gateway/health.sock
```

Gateway 进程职责：

1. 校验 UDS peer 与协议边界。
2. 通过专用 DB RPC claim 并重新验证 ModelAttempt、租户、worker、token、revision、kill epoch 和 request hash。
3. 使用现有 `get_agent_runtime_ai_bundle` 语义取得 encrypted SecretReference。
4. 在进程内由 `SecretMaterialService + LocalKEKProvider` 解密，并仅在受控 consumer 内构造一次性 Provider adapter。
5. 执行一次完整 Provider stream，写 Gateway operation facts，向 Runtime 返回脱敏流和终态摘要。
6. drain、heartbeat、stale claim recovery 与 `UNKNOWN` 收敛。

Runtime 进程职责：

1. 生成并持久化 ModelAttempt、ContextReceipt、request hash 与调用预算。
2. 通过 UDS 发起一次 Gateway operation。
3. 消费已规范化的流事件，生成 Runtime 事件和工具调用。
4. 根据 Gateway 的 `completed/failed/unknown` 结果完成 ModelAttempt；Gateway 不拥有该写权限。
5. 断连后只按 request id readback；不普通重派无法证明未执行的请求。

### 3.2 Linux 身份和文件权限

新增专用身份：

```text
user:  everydayai-agent-model-gateway
socket group: everydayai-model-gateway
secret group: everydayai-model-gateway-secret
```

`everydayai-agent-runtime` 仅作为 supplementary member 加入 socket group
`everydayai-model-gateway`，绝不加入 `everydayai-model-gateway-secret`。Gateway user 是 secret
group 的唯一服务成员；因此 Runtime socket client 不获得 Gateway 文件读取权。

Socket 目录由 systemd `RuntimeDirectory=everydayai-agent-model-gateway` 创建，mode `0750`；`gateway.sock` owner 为 Gateway user，group 为 `everydayai-model-gateway`，mode `0660`。Server 在 `accept` 后必须使用 Linux `SO_PEERCRED` 取得 pid/uid/gid，并要求 uid 精确等于 `everydayai-agent-runtime`；文件 mode/group 只是第一层门禁。非 Linux 或无法取得 peer credential 时生产启动失败关闭。

Runtime unit继续：

- 只加载 `agent-runtime-worker.env`。
- `InaccessiblePaths` 保持禁止 Backend `.env`、`.env.kek`、Gateway KEK env 和旧 model env。
- 仅允许 UDS 和 PostgreSQL 所需地址族；代码中不再构造 Provider adapter，也没有 Provider credential，因此即使主机网络策略暂不能按域名限制，也无法合法发起 Provider 请求。

Gateway unit只加载：

- `/etc/everydayai/agent-model-gateway.env`：专用 Gateway DB URL、socket、release、超时、flags。
- `/etc/everydayai/agent-model-gateway-kek.env`：仅现有 KEK keyring 两个变量，
  `root:everydayai-model-gateway-secret`、mode `0640`。

两份 Gateway env 均使用 secret group；Gateway unit 通过 `SupplementaryGroups` 读取，主 Group
仍为 socket group。Gateway 不加载 Backend `.env`、Runtime worker env 或 Provider API key env。
Gateway KEK env 由现有 `.env.kek` 的已验证值事务性派生，不创建第二份密钥语义；部署脚本不得打印值。Gateway 仅允许读取代码、这两份 env、自己的 `/run` 与必要证书；`ProtectSystem=strict`、`ProtectHome=true`、`PrivateTmp=true`、`NoNewPrivileges=true`、`PrivateDevices=true`、`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`、`RestrictSUIDSGID=true`、`LockPersonality=true`。其网络只用于专用 PostgreSQL 与已配置模型 Provider HTTPS；生产主机防火墙/egress allowlist 是部署门禁，不在应用中复制网络控制面。

## 4. UDS 协议 v2

### 4.1 帧与限制

协议名固定为 `agent-model-gateway.v2`。v2 是未部署内部严格协议的唯一合法版本；Runtime、Gateway、health 与测试必须同步升级，v1 frame 严格拒绝，不提供双协议兼容。每帧为：

```text
4-byte unsigned big-endian payload length
UTF-8 JSON object
```

约束：

- 单个 request frame 最大 `4 MiB`；单个 response frame 最大 `1 MiB`；每连接仍只允许
  一个 request，单次响应累计最大 `16 MiB`。请求不采用未定义的分片协议。
- JSON 深度最大 32，字符串最大 512 KiB，tools 最大 128 项，messages 最大 512 项；超限在 DB claim 前拒绝。
- 每连接只承载一个 request；响应结束后 server 主动关闭，禁止 multiplex，降低串线和背压复杂度。
- connect timeout 2 秒，claim/首帧 timeout 10 秒；Provider deadline 来自冻结的 `ModelRequestOptions.timeout_seconds`，Gateway 上限 120 秒；close/drain 单 operation 最长 130 秒。
- 所有 UUID、枚举、sequence 和长度严格解析；未知字段拒绝，协议 version 不匹配返回稳定错误码后断连。

### 4.2 请求

唯一生产 operation 为 `model.complete`：

```json
{
  "version": "agent-model-gateway.v2",
  "type": "request",
  "operation": "model.complete",
  "request_id": "uuid",
  "org_id": "uuid",
  "user_id": "uuid",
  "run_id": "uuid",
  "model_step_id": "uuid",
  "model_attempt_id": "uuid",
  "worker_id": "text",
  "execution_token": "uuid",
  "request_hash": "sha256",
  "state_version": 1,
  "model_id": "text",
  "provider": "text",
  "model_revision": "text",
  "purpose": "model.invoke",
  "tenant_kill_epoch": 0,
  "provider_kill_epoch": 0,
  "capability_kill_epoch": 0,
  "deadline_ms": 120000,
  "input": {
    "messages": [],
    "tools": [],
    "options": {},
    "context_receipt_hash": "sha256"
  }
}
```

Socket 字段不是授权事实。Gateway 必须把全部身份交给 claim RPC，由数据库按已持久化 Run/ModelAttempt/receipt/fence 重新验证。请求不得包含 API key、KEK、encrypted envelope、credential lease、Secret handle、数据库连接或未冻结 Settings。

### 4.3 响应与流式承载

响应帧公共字段为 `version/request_id/sequence/type`。合法类型：

- `accepted`：DB operation id、`claimed` 或 `readback`，证明 Gateway 已接管；不包含 SecretReference。
- `delta`：规范化 `text_delta/tool_call_delta/usage_delta/provider_metadata`，provider metadata 只允许白名单 request id 和 nullable provider stop reason。
- `completed`：承载 canonical `stop_reason`、nullable `provider_stop_reason`、规范化 output、完整公开 usage、provider request id、response hash 和 operation state version。每个 tool call 分别承载 Runtime `call_id` 与 nullable `provider_call_id`，不得把派生 Runtime id 冒充 Provider id。
- `failed`：仅稳定 error code、retry class=`terminal` 与脱敏摘要。
- `unknown`：ambiguity kind、response_started、provider request id（如有）与 `reconcile_only=true`。

Gateway 直接执行完整 Provider 调用。禁止通过 UDS 返回 API key、KEK、SecretReference、Python client 或可序列化 credential lease。流式 tool-call 仍使用现有 `ResponseAccumulator` 和响应解析语义；协议只承载规范化事件，不复制 Provider-specific chunk 格式。

Runtime 断连处理：

- 未收到 `accepted` 且 DB readback 证明 operation 尚未 claim/dispatch：可由同一 ModelAttempt、同一 request id 再次连接。
- 已 `accepted` 或 DB 状态为 `dispatching` 后断连：Runtime 写 `UNKNOWN`，只允许 readback/reconcile。
- `completed/failed/unknown` frame 丢失：Runtime 用 request id 调用只读 RPC；不得创建新 request id。

Runtime 以 v2 `stop_reason` 直接构造 `ModelStepResult`，不再从 Provider reason 重新推断，并按 domain 合同核对 stop reason 与 output/tool 结构。唯一 canonical response hash helper 同时由 Gateway 与 Runtime 使用，绑定 canonical stop reason、nullable Provider stop reason、output、tool call 的两类 id/name/arguments 及全部 `ModelUsage` 字段。Runtime 使用 DB reasoning usage 与 UDS 公开 usage 重建结果后重新计算 hash，并要求 wire hash、DB operation hash、重算 hash 三方一致；任一协议字段或 DB 事实不一致均收敛为 `UNKNOWN`，不普通重派。

## 5. 数据库事实、角色与 RPC

### 5.1 Additive lane

BG2 实施前重新核对 migration 账本；基于当前 baseline 的候选文件为：

```text
backend/migrations/227_18_agent_runtime_model_gateway.sql
backend/migrations/rollback/227_18_agent_runtime_model_gateway_rollback.sql
```

不得修改 223、227_16、227_17 或任何既有 migration 身份。

新增角色 `everydayai_agent_model_gateway`：`LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`，仅获得 Gateway 窄 RPC `EXECUTE`。连接时必须同时满足 `session_user=everydayai_agent_model_gateway` 与 `app.access_kind=agent_model_gateway`。

### 5.2 Gateway operation fact

新增 `agent_runtime_model_gateway_operations`，至少包含：

```text
id, request_id UNIQUE, org_id, user_id, run_id,
model_step_id, model_attempt_id, worker_id, execution_token,
request_hash, model_id, provider, model_revision, purpose,
tenant_kill_epoch, provider_kill_epoch, capability_kill_epoch,
expected_state_version, status, claim_token, lease_expires_at,
provider_request_id, response_started, response_hash,
usage_summary, ambiguity_code, created_at, updated_at
```

表不保存 messages、tools、prompt、SecretReference、ciphertext、wrapped DEK、API key、完整 Provider request/response 或原始异常。`usage_summary` 只允许 token/credit 整数与白名单单位。表启用并强制 RLS；Gateway、Runtime、legacy worker 与 PUBLIC 均无直接表权。

### 5.3 窄 RPC

所有函数均 `SECURITY DEFINER SET search_path=pg_catalog,public`，显式校验 `session_user + app.access_kind`，使用 CAS 和固定锁序：

1. `claim_agent_runtime_model_gateway_operation(...)`：Gateway-only。验证 tenant/user/run/model attempt/worker/execution token/request hash/model/provider/revision/purpose/kill epochs/state version；幂等创建或读取 request id；返回 claim token、冻结 input receipt 摘要和现有 `get_agent_runtime_ai_bundle` 等价的 encrypted bundle。它是 Gateway 唯一取得 SecretReference 的入口。
2. `mark_agent_runtime_model_gateway_dispatched(...)`：Gateway-only，在网络调用前原子写 `dispatching`；重复调用按 claim token 和 state version readback。
3. `renew_agent_runtime_model_gateway_operation(...)`：Gateway-only，只续租未终结且 epoch/revision 未变化的 operation。
4. `finalize_agent_runtime_model_gateway_operation(...)`：Gateway-only，只写 `completed/failed/unknown` 的脱敏摘要，不能修改 Runtime ModelAttempt 终态。
5. `read_agent_runtime_model_gateway_operation(...)`：Runtime Worker 与 Gateway 可执行；按 org/run/model attempt/request id/token 只读，绝不返回 encrypted bundle 或 Secret。
6. `recover_agent_runtime_model_gateway_operations(...)`：Gateway-only。`claimed` 且未 dispatch 的 stale operation 可重新 claim；`dispatching` stale operation只能收敛为 `unknown`。

切换后撤销 Runtime Worker 对旧 `get_agent_runtime_ai_bundle` 的 `EXECUTE`；Gateway 通过新 claim RPC间接复用同一 resolution。数据库不提供“明文解密 RPC”，KEK 永不进入数据库。

Rollback guard 在撤权/删对象前检查 operation facts；存在任何行时返回稳定错误并失败关闭。Migration 的 apply、rollback、reapply、ACL/RLS/search_path 与事实 guard 必须在 disposable PostgreSQL 实证。

## 6. Owner、幂等与 UNKNOWN

### 6.1 唯一 Owner

```text
Runtime:
  Run / ModelStep / ModelAttempt / retry budget / UNKNOWN / terminal / tool actions

Gateway:
  one gateway operation / Provider client lifetime / stream normalization /
  provider operation fact / claim lease
```

Gateway 的 `finalize` 只是 Provider operation 事实，不是 Runtime 终态。Runtime 必须读取该事实并通过现有 ModelAttempt CAS/receipt 合同决定完成、失败或 unknown。Gateway 无 Runtime terminal RPC 权限。

### 6.2 幂等与响应丢失

`request_id` 由 Runtime 从固定 ModelAttempt 派生并持久化；同一 request id 的绑定字段不同返回 `idempotency_conflict`。如果 Provider 支持外部 idempotency key，Gateway 使用 `sha256(request_id + request_hash + provider_revision)`，但能力必须在 Provider registry 明确声明，不能根据错误字符串推断。

当前 `create_runtime_chat_adapter` 覆盖的模型 Provider 没有已证明的 durable submit readback API，也没有统一可靠的外部 idempotency合同。因此默认规则是：

```text
DB mark dispatching
  -> one Provider network call
  -> persist completed/failed/unknown
```

Gateway/连接在 `dispatching` 后崩溃，且无法由既有终态事实证明结果时，必须写/保留 `UNKNOWN`；Runtime 不普通重派。Provider request id 只能作为人工对账证据，不能冒充 readback。未来某个 Provider 只有在单独验证 idempotency+readback 后，才可增加 provider-specific reconcile capability；不会改变默认保守合同。

## 7. 配置、Secret 与代码复用

Gateway 内部调用链固定为：

```text
claim RPC
  -> encrypted configuration bundle
  -> AsyncSecretBundleResolver-compatible projection
  -> SecretMaterialService
  -> LocalKEKProvider
  -> plaintext exists only in Gateway call frame
  -> create_runtime_chat_adapter consumer
  -> Provider adapter.stream_chat
  -> adapter.close
  -> plaintext references released
```

配置 resolution 保持现有 `_resolve_configuration_bundle` 与 `get_agent_runtime_ai_bundle` 的组织/用户/平台 fallback。Gateway 不新增配置表、模型订阅、默认模型或 credential handle SSOT。显式模型选择和 smart default 仍由 `model_resolution.resolve_runtime_model` 在 Runtime 冻结，Gateway 只验证，不重新选择。

为避免复制模型业务逻辑：

- `PostgresModelCallFactory` 留在 Runtime，继续产生 frozen request、ContextReceipt 与 Toolset。
- 将 `ExistingProviderModelAdapter` 中纯 Provider stream 消费、`ResponseAccumulator`、tool-call normalization 和错误分类提取为共享、无 Secret 状态的模块；Gateway 调用它。
- `create_runtime_chat_adapter` 留在 Gateway trust boundary，被 Secret consumer 调用。
- Runtime 新增 `ModelGatewayClient` 实现现有 `ModelPort`，通过 UDS 收发规范化事件；Runtime 不导入/调用 Provider factory。
- Provider adapter 及其解析不在 Gateway 重写；UDS codec 不理解 Provider-specific chunk。

### 7.1 f721557e 参考边界

只复用概念：

- opaque identity binding：tenant/provider/revision/purpose/run/worker/token/model context。
- one-use/short-lived material consumer。
- backend unavailable、binding mismatch、expiry 的 failure-closed 语义。
- Secret-free audit 与不可序列化材料边界。
- core model credential capability 不依赖 ERP/Media/Scheduler service bundle。

不采用代码：

- 不 cherry-pick `f721557e`。
- 不在 Runtime 进程构造 `ConfigurationCredentialBackend`、`SecretMaterialService` 或 `LocalKEKProvider`。
- 不让 `CredentialLease`、raw Secret 或 Provider client进入 `ModelStepRequest` 或跨 UDS。
- 不以“adapter 可测试”标记 production bridge ready。

## 8. Readiness、健康与恢复

Gateway readiness 依赖：

```text
release/config valid
-> UDS bound with expected ownership/mode
-> DB role + claim/readback RPC available
-> KEK env parse + LocalKEKProvider available
-> configuration bundle schema registry available
-> Provider adapter registry available
```

启动不探测全部租户 credential，也不调用真实 Provider。租户 bundle、revision、capability 和 kill epoch 在每次 claim 时 lazy 验证。缺任一依赖返回 `unavailable/degraded`，不得伪造 ready。

健康使用独立 `health.sock`，只返回 version、release SHA、ready/draining、DB/KEK/provider-registry 的枚举摘要、in-flight 数和最近 heartbeat；不返回路径、DSN、异常、Secret 或 tenant 数据。新增 Gateway heartbeat RPC和低基数 metrics：claim latency、in-flight、completed/failed/unknown、disconnect、stale recovery、KEK/bundle/provider unavailable。标签仅 provider/error_code/release，不使用 org/run/request id。

Shutdown/drain：SIGTERM 后停止接受新 operation，继续已 claim operation至 130 秒 deadline；未 dispatch 的 claim 释放/过期可恢复，已 dispatch 且无法持久化终态的 operation 收敛为 `unknown`。进程退出前关闭 Provider adapter、DB 和 sockets。重启 recovery 只重新 claim `claimed`；`dispatching` 不重发。

日志只允许 request id 的短 hash、operation id、provider、model revision、状态和稳定错误码。异常按类型映射，禁止原始 message、headers、URL query、payload、prompt、tool args、API key、KEK、ciphertext、wrapped DEK 和文件路径。

## 9. D0-A flags-off 部署扩展

BG5 扩展现有 control-plane release transaction：

- `deploy/provision-control-plane-worker-envs.py`：从已批准的生产来源生成 `agent-model-gateway.env`，并以不输出值的方式复制/验证最小 `agent-model-gateway-kek.env`；两者加入同一 backup/restore transaction。
- `deploy/everydayai-agent-model-gateway.service`：新增专用 unit。
- `deploy/check-control-plane-unit-manifest.sh`：reviewed SHA manifest 从三个 unit 扩为四个 control-plane unit。
- `deploy/update-control-plane-units.sh`、`deploy/install-service-units.sh`、`deploy/runtime-flags-off-install.sh`：env 与 unit 全量预检后事务安装，失败统一恢复。
- `deploy/check-agent-runtime-unit-states.sh`：Gateway 纳入 pre/post `inactive:disabled` 门禁。
- `deploy/env-templates/agent-model-gateway.env.template` 与最小 KEK template只含示例，不含真实值。

flags-off 安装只放置 env/unit并 `daemon-reload`，Gateway 与 Runtime 均保持 inactive、disabled，所有 production flags false；不应用 migration、不启动进程、不切 Owner。Sandbox 的 env、unit、hash 和状态均不读取、不修改。

## 10. 实施批次

### BG1：Protocol 与 local isolated harness

依赖：BG0。

写集合：

- `backend/services/agent/runtime/model_gateway/__init__.py`
- `backend/services/agent/runtime/model_gateway/protocol.py`
- `backend/services/agent/runtime/model_gateway/client.py`（仅 isolated transport）
- `backend/services/agent/runtime/model_gateway/server.py`（仅 fake handler）
- `backend/tests/test_agent_runtime_model_gateway_protocol.py`
- `backend/tests/test_agent_runtime_model_gateway_local_harness.py`

门禁：framing/limit/version/unknown field/timeout/backpressure/sequence/断连/SO_PEERCRED fake contract；流式 text/tool/usage roundtrip；Secret/KEK/envelope 禁止字段；仅本地临时 UDS，无 DB、Secret、Provider、migration。Commit 独立且 `production_ready=false`。

### BG2：DB facts、角色与 RPC

依赖：BG1 protocol identity 已冻结。

写集合：

- `backend/migrations/227_18_agent_runtime_model_gateway.sql`（实施前核对编号）
- 对应 rollback
- `backend/services/agent/runtime/infrastructure/postgres/model_gateway.py`
- migration/static/disposable PostgreSQL 测试
- 必要的 `docs/PROJECT_OVERVIEW.md`、`docs/FUNCTION_INDEX.md`

门禁：Gateway role、claim/dispatch/renew/finalize/readback/recover、租户/用户/run/attempt/worker/token/hash/model/provider/revision/purpose/epoch/state CAS、并发单赢家、RLS/FORCE、ACL、fixed search_path、Runtime旧 bundle RPC撤权、事实 rollback guard、apply→readback→guard→cleanup→reapply→rollback。无 Secret 解密或真实 Provider。

### BG3：Gateway process、Secret 与 Provider execution

依赖：BG2 verified。

写集合：

- `backend/agent_model_gateway_main.py`
- `backend/services/agent/runtime/model_gateway/service.py`
- `backend/services/agent/runtime/model_gateway/configuration.py`
- `backend/services/agent/runtime/model_gateway/provider.py`
- 从 `infrastructure/model/adapter.py` 提取的共享 stream normalization 模块
- Gateway process/config/secret/provider mock tests

门禁：仅 Gateway 构造 `LocalKEKProvider + SecretMaterialService`；现有 bundle fallback、显式/default model回归；fake envelope 解密；Secret不出现在 UDS、repr、pickle、JSON、log、exception、receipt/event/artifact；Provider builder只在受控作用域得到材料；真实 Secret/Provider禁止；crash/drain/unknown实证；平台仍不 ready。

### BG4：Runtime client 与 composition

依赖：BG3 verified。

写集合：

- production `model_gateway/client.py`
- `backend/services/agent/runtime/production_model.py`
- `backend/services/agent/runtime/composition.py`
- `backend/services/agent/runtime/production_factory.py`
- `backend/services/agent/runtime/infrastructure/model/adapter.py` 及受影响 ports/tests

门禁：`PostgresModelCallFactory` 不再取得 credential lease；`ModelStepRequest` 不含 Secret材料；Runtime production只使用 `ModelGatewayClient`；不得导入 Provider factory/KEK/config decrypt；模型选择、stream/tool-call、usage、UNKNOWN、cancel、response loss回归；Gateway缺失失败关闭；ERP/Media/Scheduler/Sandbox保持关闭；flags与ready false。

### BG5：Deploy、CI 与统一验收

依赖：BG1～BG4 verified。

写集合：

- Gateway systemd unit和两个 env template
- D0-A control-plane provisioning/update/install/state/manifest脚本
- disposable CI workflow与部署静态测试
- Runbook、Project Overview、Function Index

门禁：专用 user、socket/secret group、DB role、socket mode和peer UID、Runtime通过 UNIX DAC
不可读 Gateway DB/KEK env、Gateway只读最小 KEK env、四 unit flags-off事务/自动恢复、Sandbox零 diff、unit hash manifest、local UDS+disposable PostgreSQL+mock Provider E2E、crash/response loss/unknown/drain、敏感扫描。生产只允许 flags-off 安装；统一验收前不启动、不迁移、不部署 Provider调用。

每批都必须运行定向测试、受影响 Runtime/model/configuration 回归、Python compile、`git diff --check` 和 `scripts/check_task_change.py`；工作区干净后交付，不推送、不部署。

## 11. 发布、回滚与停止条件

数据库 migration 是 forward-only production contract。生产一旦应用，不执行 destructive rollback SQL；binary 回滚流程为：

1. flags 保持/恢复 false，停止新 ModelAttempt dispatch。
2. Gateway drain；`dispatching` ambiguity 保留为 unknown。
3. 回退到仍能忽略新增 additive objects 的上一版 binary/unit。
4. 保留 Gateway operation facts和审计，不恢复 Runtime 的 KEK/旧 model env/旧 bundle直权。
5. 通过新 additive migration或 binary forward fix 修复。

Rollback SQL 只用于 disposable/staging，并在存在 operation facts时失败关闭。不能通过回滚重新赋予 Runtime Worker 解密或 Provider执行权。

实施停止条件：需要改变现有配置 fallback/模型选择产品语义；需要公共 API；需要 Runtime持有 KEK/raw Secret；需要数据库返回明文；Provider业务逻辑必须复制而不能共享；migration lane与其他分支冲突；无法保证 Runtime/Gateway唯一Owner或UNKNOWN禁止重派。普通文件拆分、命名、fixture和局部错误处理由执行者自行决定。

## 12. 最终验收结论格式

BG5 前只能报告各批 `verified` 和 `production_ready=false`。只有 BG1～BG5、独立安全复审、disposable CI和获批的flags-off安装全部通过，才能形成生产启用候选；真实启用、迁移、服务启动、Owner切换仍需单独生产授权。

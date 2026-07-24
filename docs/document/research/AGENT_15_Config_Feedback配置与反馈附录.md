# AGENT 15 附录：Config / Feedback 配置与反馈

> 主文档：[AGENT_15_Observability_Config可观测性与配置运行时.md](AGENT_15_Observability_Config可观测性与配置运行时.md)
> 内容：Config Runtime、Feedback、边界风险、方案对比、差距矩阵与实施顺序

## 7. 目标 Config Runtime

### 7.1 Config Catalog

每个配置项登记：

```text
key / type / default / validator
scope: deployment | org | user | agent | run | action
sensitivity
owner
sources / precedence
apply_mode
restart_requirement
policy_clamp
deprecated_alias
```

禁止新增“随便放进 Settings 的字段”。Secret 与普通 Runtime knob 使用不同存储和读取
接口。

### 7.2 推荐优先级

普通 Runtime 配置从低到高：

```text
code default
→ deployment config
→ remote platform config
→ org config
→ user preference
→ Agent definition
→ request override
→ Policy clamp
```

但不是所有字段允许所有层。例如 Provider secret 只允许 deployment/org secret store；
安全上限和可用工具由 Policy 最后求交，request 只能收紧，不能放宽。

Resolver 输出每个 key 的：

```text
effective_value
source
source_revision
resolved_at
policy_clamped
```

### 7.3 EffectiveConfigSnapshot

Run 启动时保存：

```text
config_snapshot_id
catalog_version
agent_definition_revision
tool_catalog_revision
policy_revision
model_catalog_revision
effective value hashes
selected non-secret values
secret references（不复制 secret）
```

Action 创建时可再冻结 Executor-specific snapshot。审计和重放能解释“当时为什么选这个
模型、超时、成本和工具集”，配置更新只影响新 Run/Action。

### 7.4 Apply Mode

| 模式 | 示例 |
|---|---|
| immediate | UI theme、非安全展示设置 |
| next_step | progress 节流等体验参数 |
| next_action | Provider timeout、model routing |
| next_run | Agent、Tool Catalog、Context budget |
| restart | DB pool、加密主密钥、进程级 exporter |
| immutable | 已创建 Action 的授权、成本上限、幂等键 |

高权限 kill switch 可立即阻止尚未提交的 Action；已被 Provider accepted 的 Action 进入
cancel/reconcile，不能假装从未执行。

### 7.5 更新与回滚

配置发布：

1. schema/type validation。
2. cross-field validation。
3. Policy clamp。
4. dry-run 影响范围。
5. 写 revision 和审计。
6. 发布 invalidation。
7. 各进程拉取并构建 candidate。
8. candidate 成功后原子替换 last-known-good。
9. 记录 applied/rejected。

缓存必须带 revision 和 TTL；收到 invalidation 后主动失效。配置服务不可用时：

- 已运行 Run 使用 snapshot。
- 新 Run 使用最近 last-known-good，若安全关键配置过期则 fail-closed。
- Secret 读取失败不回退到其他租户值。

## 8. Feedback 与 Evaluation 闭环

统一 Feedback：

```text
feedback_id
message_id / run_id / action_id / artifact_id
user_id / org_id / channel
rating / reason_codes / optional_text
model / agent / config revisions
solicited / created_at
```

Web 点赞踩、retry/regenerate、企微反馈和人工纠正都映射为不同 signal，不把 retry
简单等价为负评。反馈文本默认不进 Metrics；Evaluation pipeline 按 consent 和脱敏规则
读取。

反馈用于：

- 按模型/Agent/Skill/Tool 版本分群。
- 找失败链路并构建回归集。
- 评估路由、Prompt 和配置变更。
- 触发人工复核。

不能直接让单条反馈在线修改 Prompt、模型权重或权限配置。

## 9. 边界与风险

| 场景 | 策略 |
|---|---|
| Telemetry sink 不可用 | 异步降级，业务继续，记录 dropped/export health |
| Audit DB 不可用 | Outbox/有界 spool；高风险审计失败按 Policy 决定阻断 |
| 高基数爆炸 | schema 阶段拒绝 identifier metric label |
| Secret 出现在异常 | emit scrub + export validator |
| ContextVar 跨 Worker 丢失 | 在 Task/Event/Callback 显式携带关联 ID |
| 配置解析失败 | 保留 last-known-good，记录 rejected |
| 安全配置过期 | 新高风险 Action fail-closed |
| 热更新与在途 Action 竞态 | Action snapshot + policy kill switch |
| 多进程 revision 不一致 | applied revision gauge + readiness gate |
| Usage 重复回调 | usage source id 幂等 |
| Provider 不报 usage | estimated + incomplete |
| SubRun 归因迟到 | Parent ledger fold，保留 unattributed 状态 |
| Error sink queue 满 | dropped counter + sampled fallback |
| 用户撤回观测 consent | 停止后续外发，按保留策略处理历史 |
| Feedback 重复提交 | feedback id / channel event id 幂等 |

## 10. 方案对比

| 维度 | A：继续扩充日志/Sentry/Langfuse | B：统一 schema + 多 Sink | C：自建全套平台 |
|---|---|---|---|
| 改造成本 | 低 | 中 | 高 |
| 跨链路归因 | 弱 | 强 | 强 |
| 隐私治理 | 分散 | 集中 | 集中 |
| Vendor lock-in | 高 | 低 | 低 |
| 运维成本 | 中 | 中 | 高 |
| 分阶段迁移 | 容易但持续分叉 | 容易 | 困难 |

推荐 B。先定义 vendor-neutral schema/context，再将 Loguru、Sentry、Langfuse、DB Audit
和未来 OTEL 作为 Sink。当前阶段不决定最终 collector，也不建设自研监控 UI。

## 11. 第一轮差距矩阵

| 能力 | Grok | 当前项目 | 目标 |
|---|---|---|---|
| Telemetry schema | typed closed schema | 文案/多表/部分 dataclass | versioned typed schema |
| Context | Session/Prompt task-local | task trace ContextVar | Run/Action context |
| Tracing | tracing + OTEL + artifact | Langfuse 少量接入 | vendor-neutral spans |
| Usage | token 类型完整 | 多调用点不完整 | append UsageLedger |
| Metrics | OTEL logs/metrics | DB rows + 日志指标 | bounded OTel metrics |
| Redaction | default-deny + export validator | 局部脱敏 | 多 Sink allowlist |
| Feedback | 本地持久 + signals + remote | retry metric/企微日志 | 统一 Feedback fact |
| Config layers | managed/user/requirements/MDM | env + org +散落文件 | Catalog + Resolver |
| Hot reload | typed update + LKG | 大多需重启 | 分 apply mode |
| Execution snapshot | 部分 Session snapshot | 无统一快照 | Run/Action snapshot |

## 12. 实施边界与顺序

1. 先定义 TelemetryContext、事件 schema 和禁止字段。
2. 将 Run/Action/Provider/Delivery 的稳定 ID 贯通。
3. 用 Adapter 接入现有 Loguru、ToolAudit、KnowledgeMetrics 和 Langfuse。
4. 建 UsageLedger，先旁写校验，不立刻替换积分账本。
5. 补关键低基数指标和 export health。
6. 统一 Sentry/Error/Alert 脱敏与 typed error code。
7. 建 Config Catalog 与 EffectiveConfigSnapshot。
8. 对配置项逐个标注 apply mode，不做全量热更新。
9. 统一 Web/企微 Feedback fact。
10. 观测新旧一致后再删除日志 regex 指标和重复配置读取。

本轮不修改生产代码，不新增依赖。最终文件、表、Collector、Dashboard 和告警阈值在全项目
差距矩阵完成后统一设计，避免先搭一套空监控框架。

## 13. 本轮结论

EVERYDAYAIONE 已有足够多的观测组件，问题不是“缺 Sentry/Langfuse”，而是它们没有
围绕统一 Run/Action 事实模型组成系统。配置同样不是“字段不够”，而是缺少来源、版本、
生效边界和执行快照。

应吸收 Grok 的 typed schema、task-local correlation、低内容模式、default-deny
redaction、export validator、last-known-good 和 typed hot reload；同时利用 SaaS 数据库
优势补齐 UsageLedger、租户配置、持久审计和 EffectiveConfigSnapshot。

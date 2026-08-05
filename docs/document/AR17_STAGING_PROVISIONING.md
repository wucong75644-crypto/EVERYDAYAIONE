# AR-17 Staging Provisioning Checklist

状态：资源 provisioning 包；不代表 staging ready，不包含真实地址、凭证或 Secret。生产 flags 必须保持关闭。

## 0. 自动化门禁命名

仓库内的 GitHub Actions disposable workflow 只允许产生以下结论：

```text
C6 Disposable Freeze Candidate Verified
```

该 workflow 使用 disposable PostgreSQL、LocalNonProductionCredentialBackend、LocalNonProductionObjectStore 和 isolated mock Provider，并覆盖 C1～C6.2、227_13～227_15 及既有 A1～A10 合同。它只冻结本地/CI 候选证据，不能产生 `T5-Staging Ready`、production verified 或 production readiness 结论。

`T5-Staging Ready` 仍要求独立外部 staging 资源、真实 readback、Worker heartbeat、rollback 权限和完整观察证据。对应 workflow 失败时，不得通过跳过测试、删除断言或修改 production contract 绕过。

## 1. 资源清单与责任

| 资源 | 责任角色 | 必须提供 | 验收结果 |
| --- | --- | --- | --- |
| staging PostgreSQL | DB/基础设施 | 独立数据库、admin URL、Runtime worker URL、Projection/Authorization/Sandbox role | 227_02→227_05 apply、readback、rollback guard、reapply |
| Credential backend | 安全/平台 | opaque handle、tenant/provider/revision/purpose、短 lease、审计脱敏 | A1 resolver 交叉租户/过期/revision 失败关闭 |
| Object Store | 平台 | tenant-scoped bucket/prefix、verified readback、delete/restore | Artifact hash、lineage、Workspace 恢复 |
| ERP Provider | ERP/Provider 负责人 | 隔离账号或批准 mock、provider revision、readback/cancel API | submit、UNKNOWN、reconcile、cancel、幂等 |
| Media Provider | Media/Provider 负责人 | 隔离账号或批准 mock、状态查询和取消接口 | submit、readback、UNKNOWN、reconcile |
| Runtime Worker | Runtime/运维 | `everydayai_agent_runtime_worker`、`AGENT_RUNTIME` access kind | heartbeat、claim、lease、fencing、dead recovery |
| Projection Worker | Runtime/运维 | Projection role 与服务环境 | projection backlog/dead/requeue |
| Authorization Worker | 安全/运维 | Authorization role 与服务环境 | PolicyReceipt、Dispatch Gate、recovery |
| Sandbox Worker | Sandbox/运维 | Linux sandbox、nsjail、cgroup、只读 rootfs | capability probe、crash/restart/cleanup |
| Web/WeCom ingress | 接入负责人 | staging 域名、隔离回调、非生产 app 配置 | ingress、SAFE/CONFIRM/DANGEROUS、投影 |
| 回滚权限 | 发布负责人 | 关闭 Runtime claim gate、恢复兼容 ingress 的权限 | rollback 不删 facts、不恢复宽权限 Owner |

## 2. 环境变量与角色合同

只能在 staging secret manager 或受限 service environment 中配置，禁止提交仓库：

```text
WORKER_DATABASE_URL=<staging runtime role URL>
RUNTIME_ADMIN_DATABASE_URL=<staging runtime admin URL>
AGENT_RUNTIME_PROCESS_ROLE=agent_runtime
AGENT_RUNTIME_INGRESS_ENABLED=false
AGENT_RUNTIME_PRODUCTION_ENABLED=false
CREDENTIAL_BACKEND_PROFILE=<approved non-production profile>
OBJECT_STORE_PROFILE=<tenant-scoped staging profile>
ERP_PROVIDER_PROFILE=<isolated profile>
MEDIA_PROVIDER_PROFILE=<isolated profile>
```

Runtime worker 只能执行窄 `SECURITY DEFINER` RPC；不得获得 facts/业务表直权。每个函数必须固定 `search_path=pg_catalog,public`，显式验证 `app.access_kind`、tenant、run/action/attempt、revision、request hash 和 fencing token。

## 3. 资源到位后的验收顺序

在 staging shell 中设置测试环境变量后执行，不得使用生产 URL：

```bash
RUN_AR17_1_DB_TEST=1 \
PYTHONPATH=backend \
PYTHONPYCACHEPREFIX=/private/tmp/ar17-pyc \
backend/venv/bin/pytest -q -m external \
  backend/tests/test_agent_runtime_ar174_a7_provider_binding_postgres_external.py \
  backend/tests/test_agent_runtime_ar174_a2_postgres_external.py \
  backend/tests/test_agent_runtime_a8_scheduler_cas_postgres_external.py
```

然后依次执行：

1. 227_02、227_03、227_04、227_05 apply；catalog/toolset/provider/facts readback。
2. RLS/FORCE RLS、worker ACL、legacy role 无 Runtime RPC、固定 search path。
3. 启动 Runtime、Projection、Authorization、Sandbox Worker。
4. 使用 `deploy/runtime-worker-db-probe.py` 和 `deploy/runtime-healthcheck.py` 验证 role、heartbeat、readiness、dead recovery。
5. 验证 Web/WeCom、SAFE/CONFIRM/DANGEROUS、PolicyReceipt、Dispatch Gate。
6. 验证 ERP/Media submit/readback/UNKNOWN/reconcile/cancel 和 external idempotency key。
7. 验证 Scheduler CAS 并发单赢家、lease、fencing、recovery。
8. 验证 Artifact/Object Store verified readback、lineage、Workspace delete/restore、Child Run cancel/recovery。
9. 注入 PostgreSQL response loss、worker crash、restart、drain、shutdown；确认 UNKNOWN 不普通重派。
10. 关闭 Redis 或使其不可用，确认正确性事实不依赖 Redis。

## 4. 回滚与停止条件

任何 tenant isolation、ACL、revision fence、credential lease、readback、CAS、恢复或副作用唯一性失败，立即停止后续步骤，保持 production flags 和 Runtime claim gate 关闭。

回滚顺序：停止新 Runtime ingress/claim → 保留 accepted/unknown reconcile → drain 当前 lease → readback facts → 仅恢复尚未产生外部副作用的兼容入口。不得删除历史 facts，不得把 ambiguous submission 交给普通 retry，不得恢复旧宽权限 Owner。

migration rollback 只能在 facts 为空且 guard 通过时执行；顺序为 227_05、227_04、227_03、227_02，随后 reapply 验证。任何事实存在时 rollback 必须失败关闭。

## 5. T5 通过门槛

只有上述资源均已独立验收、所有测试日志和 readback 证据归档、rollback rehearsal 成功，才能标记 `T5 staging ready`。本地 disposable profile 只能标记 `local disposable verified`，不能替代 staging 或 production readiness。

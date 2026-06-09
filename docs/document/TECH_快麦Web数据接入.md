# TECH: 快麦 Web 数据接入

> **状态**：生产就绪（2026-06-09）
> **代码**：`backend/services/kuaimai_external/` + `frontend/src/{pages,components,services}/kuaimai*`
> **数据库**：migration 114

## 1. 背景与定位

### 解决什么问题
快麦官方开放 API 不覆盖以下关键报表：
- **智库利润表**（erp.superboss.cc/kmzk）：含收入/退款/成本/利润全套财务核算
- **销售主题报表**（viperp 路径）：按 SKU/分销商/品牌/订单的销售明细

这些数据**Web 后台可见但官方 API 拿不到**。本模块通过 Cookie 鉴权抓取 Web 后端 JSON 接口，定期同步入库，让员工通过 AI 或 SQL 自助查询。

### 不要混淆
- `backend/services/kuaimai/` = 快麦**官方 API**客户端（signature 鉴权）
- `backend/services/kuaimai_external/` = 本模块 = **Web 后端**抓取（cookie 鉴权）
- 两套机制完全独立，可以并行使用

---

## 2. 架构

```
┌─────────────────────────────────────────────────────────────┐
│ 管理员一次性配置（每企业每数据源各 1 次）                    │
│   登录快麦 → F12 → Copy as cURL → 前端配置页粘贴            │
│        ↓                                                     │
│   POST /api/admin/kuaimai/credentials                       │
│        ↓                                                     │
│   curl_parser 自动提取 cookie + companyid                   │
│   cookie_crypto 加密 → 存 kuaimai_external_credentials      │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 后端自动调度（每天 10:00 Asia/Shanghai）                    │
│   scheduler.kuaimai_external_sync_loop                      │
│        ↓                                                     │
│   sync_all_active 遍历所有 active 凭证                      │
│        ↓                                                     │
│   per source: thinktank_sync / viperp_sync                  │
│     ├─ http_base 调快麦（_censeid + companyid）             │
│     ├─ 响应解析 → 业务列 + raw_payload 全字段留底           │
│     ├─ UPSERT (org_id, ...) → erp_*_* 表                   │
│     ├─ 店铺-运营提取 → erp_shop_operators + erp_operators   │
│     ├─ 运营自动匹配企微（按 wecom_employees.name）          │
│     ├─ field_auditor 检测字段变化 → kuaimai_field_audit    │
│     └─ wecom_alert 推告警（复用 erp_sync_healthcheck 链路）│
│                                                              │
│   错误处理：                                                  │
│     - Cookie 失效 → 自动 mark expired + 告警                │
│     - 网络错误 → tenacity 3 次重试                          │
│     - 业务异常 → 记 sync_logs + 不影响其他 org              │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 员工查询（无感）                                              │
│   ERPAgent / 直接 SQL → erp_thinktank_profit_shop / ...    │
│        ↓ OrgScopedDB 自动 WHERE org_id = ?                 │
│   返回员工所属 org 的数据                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 数据模型（migration 114）

7 张表：

| 表 | 行级隔离 | 列数 | 作用 |
|---|---|---|---|
| `kuaimai_external_credentials` | org_id | 13 | 凭证管理（cookie 加密存储）|
| `erp_thinktank_profit_shop` | org_id | 105 | 智库利润数据（98 业务列 + raw_payload）|
| `erp_viperp_sale_finance` | org_id | 69 | viperp 销售数据（56+ 业务列 + raw_payload）|
| `kuaimai_sync_logs` | org_id | 13 | 同步日志（前端展示历史） |
| `kuaimai_field_audit` | org_id | 13 | 字段/运营/店铺变化审计 |
| `erp_shop_operators` | org_id | 14 | 店铺 → 运营名映射（自动同步） |
| `erp_operators` | org_id | 13 | 运营 → 企微账号映射（管理员维护） |

### 关键设计

**店铺 vs 运营拆 2 张表的原因**：
- 同一运营管多个店铺时，企微绑定不应重复存储
- 店铺归属变化（廖晴宇 → 张三）时，廖晴宇的企微绑定不应被清空
- 拆开后：`erp_shop_operators` 记"哪个店铺归谁"，`erp_operators` 记"谁绑哪个企微"

**业务列 + raw_payload 双重保险**：
- 智库响应 338 字段，viperp 响应 62 字段
- 我们建独立列存"通用业务字段"（智库 98，viperp 56），方便 SQL 直查
- **完整原始 JSON 全部存 `raw_payload` JSONB**，任意字段都能查到（即使没建列）
- 快麦未来加新字段 → 自动进 raw_payload，零代码改动

---

## 4. 字段变化自愈

### 三类变化自动检测 + 推告警

1. **快麦响应字段结构变化**（field_auditor）
   - 每次 sync 后对比"上次字段集合"
   - 新增/消失/类型变化 → 写 `kuaimai_field_audit` + 推企微
   - 数据全在 raw_payload，业务无中断
   - 管理员决定是否手动 ALTER 加独立列

2. **店铺变化**（shop_operator_sync）
   - 新店铺 → INSERT + 告警
   - 店铺消失 → is_active=FALSE（保留历史）
   - 店铺换运营（廖→张）→ UPDATE + 告警

3. **运营变化**（operator_resolver）
   - 新运营 → 尝试按 name 自动匹配 wecom_employees → 自动绑定
   - 多匹配/找不到 → INSERT unbound + 告警
   - 已绑定运营企微账号失效 → 自动解绑 + 告警（自愈机制）

### Cookie 失效自愈
任何 sync 检测到 `"会话异常，请重新登录"`：
- 自动 `mark_expired` 凭证
- 推企微告警提示管理员重新粘贴 cURL
- 下次定时任务自动跳过该凭证

---

## 5. 多租户安全

| 层级 | 机制 |
|---|---|
| API 路由 | `_require_admin` 校验 org_role ∈ (owner, admin) |
| DB 隔离 | 7 张表全部加入 `TENANT_TABLES`，OrgScopedDB 自动 `WHERE org_id` |
| Cookie 加密 | 复用 organizations.encrypt_key (per-org AES-256-GCM) |
| 凭证脱敏 | 前端 API 只返回 `censeid_preview`，不暴露完整 cookie |
| 跨 org 防误删 | DELETE 操作显式 `.eq("org_id", x)` 二次校验 |

---

## 6. 调度配置

```
触发：scheduler.kuaimai_external_sync_loop
时间：每天 10:00 Asia/Shanghai
       （快麦 T+1 计算 ~8:20 完成，10:00 留充足缓冲）

分层兜底（_decide_backfill_days）：
  - 月 1 号  → 抓过去 90 天（捕捉季度对账修正）
  - 周一    → 抓过去 30 天（捕捉月度成本重算）
  - 其他    → 抓过去 7 天 （捕捉退款/售后修正）

启动：main.py asyncio.create_task(kuaimai_external_sync_loop())
失败退避：异常后 sleep 3600s 重试
```

---

## 7. API 一览

```
GET    /api/admin/kuaimai/credentials               列出本 org 凭证
POST   /api/admin/kuaimai/credentials               粘贴 cURL 创建/更新
DELETE /api/admin/kuaimai/credentials/{id}          删除
POST   /api/admin/kuaimai/credentials/{id}/test     测试连接（探活）
POST   /api/admin/kuaimai/sync/{source}             手动触发同步
GET    /api/admin/kuaimai/sync-logs                 同步记录
GET    /api/admin/kuaimai/operators                 运营列表（含店铺数）
PATCH  /api/admin/kuaimai/operators/{id}/bind       手动绑定企微
PATCH  /api/admin/kuaimai/operators/{id}/unbind     手动解绑
```

权限：仅 owner / admin（前端入口隐藏 + 后端 `_require_admin`）

---

## 8. 前端

路由 `/settings/integrations/kuaimai`，3 tabs：

| Tab | 功能 |
|---|---|
| 📊 数据源 | 智库 + viperp 凭证卡片 / 粘贴 cURL 配置 / 测试 / 立即同步 |
| ⏱️ 同步记录 | 最近 50 条 sync_logs（含错误详情）|
| 👥 运营管理 | 列出所有运营 + 店铺数 + 绑定状态 / 手动绑定 wecom_userid |

---

## 9. 生产部署检查清单

### 部署前
- [ ] 应用 migration 114（`venv/bin/python scripts/apply_migration_114.py`）
- [ ] 确认 `organizations.encrypt_key` 已配置（migration 103 应该已经做了）
- [ ] 重启 backend（让 `kuaimai_external_sync_loop` 启动）
- [ ] 重启前端构建（让 `/settings/integrations/kuaimai` 路由生效）

### 验证
- [ ] 管理员浏览器打开 `/settings/integrations/kuaimai`
- [ ] 粘贴 cURL 配置至少一个数据源
- [ ] 点击"测试连接" → 绿色提示
- [ ] 点击"立即同步" → toast 显示落库行数
- [ ] SQL 查 `SELECT COUNT(*) FROM erp_thinktank_profit_shop` 看真实数据
- [ ] 看企微是否收到任何告警（首次同步会推"新运营"告警）

### 监控
- [ ] 关注 backend 日志的 `kuaimai_external_sync` 关键字
- [ ] 每天上午 10:00 后 5 分钟检查 `sync_logs` 是否有 `status=success` 记录
- [ ] Cookie 失效告警送达企微 = 自愈机制工作

### 故障排查
| 症状 | 排查 |
|---|---|
| 同步全部失败 | 看 `sync_logs.error_message`；最常见是 cookie 失效，重新粘贴 cURL |
| 没收到企微告警 | 检查 `org_members.role=owner` + `wecom_user_mappings` 是否有该用户 |
| 数据有差异 | 看 `kuaimai_field_audit` 是否有 `audit_type=field_change` 待处理 |
| 调度没跑 | grep main.py 日志 `kuaimai_external_sync_loop started` |

---

## 10. 待优化（非阻塞）

- [ ] ERPAgent 集成（让 AI 直接查这些新表）
- [ ] 字段变化"一键 ALTER SQL"前端按钮
- [ ] 单元测试覆盖（当前 0 个 test，全靠端到端跑数据验证）
- [ ] viperp 多维度（目前只跑 `dimension=shop`，未来扩 SKU/item/day/brand/distributor）
- [ ] 同步记录详情页（当前只列表，详情需要看 DB）

---

## 11. 关键文件索引

```
backend/
├── migrations/114_kuaimai_external_data.sql       (7 张表 DDL)
├── services/kuaimai_external/
│   ├── __init__.py
│   ├── curl_parser.py                             (cURL 自动解析)
│   ├── http_base.py                               (通用 HTTP + 失效检测)
│   ├── cookie_crypto.py                           (per-org AES 加解密)
│   ├── credential_store.py                        (凭证 CRUD)
│   ├── field_auditor.py                           (字段变化检测)
│   ├── wecom_alert.py                             (复用现有告警链路)
│   ├── operator_resolver.py                       (按 name 匹配企微)
│   ├── shop_operator_sync.py                      (店铺/运营变化逻辑)
│   ├── thinktank_sync.py                          (智库同步主流程)
│   ├── viperp_sync.py                             (viperp 同步主流程)
│   └── scheduler.py                               (每天 10:00 调度)
├── api/routes/kuaimai_external.py                 (9 admin endpoints)
├── scripts/
│   ├── apply_migration_114.py
│   ├── sync_thinktank.py                          (手动触发智库 sync)
│   ├── sync_viperp.py                             (手动触发 viperp sync)
│   └── verify_step2_kuaimai_external.py           (端到端验证)
└── core/org_scoped_db.py                          (+7 表名到 TENANT_TABLES)

frontend/
├── services/kuaimaiExternal.ts                    (API 封装)
├── components/integrations/KuaimaiIntegrationPanel.tsx  (主面板 + 3 tabs)
├── pages/KuaimaiIntegration.tsx                   (页面入口)
└── App.tsx                                        (路由注册)
```

# 技术设计：订单凌晨分层对账

## 背景

增量同步覆盖 99.8%+ 订单，但存在时间差导致的少量遗漏（约 0.1-0.2%）。
需要 T+1 兜底对账机制，保证历史数据 100% 准确。

采用**分层对账**策略（大厂标准方案）：先 COUNT 粗对，差异时段再拉明细补漏。

---

## 1. 现有代码分析

### 已阅读文件
- `erp_sync_scheduler.py` — 调度器，按间隔入队 Redis Sorted Set，`daily_maintenance` 通过 `DAILY_INTERVAL=86400` 控制
- `erp_sync_worker_pool.py` — Worker 从 Redis 取任务，路由到 handler，支持分布式锁+企业并发限制
- `erp_sync_executor.py` — `run_daily_maintenance()` 执行归档+聚合兜底+删除检测
- `erp_sync_handlers.py` — `sync_order()` 双维度拉取 + `_build_order_rows()` 构建行
- `erp_sync_persistence.py` — `upsert_document_items()` 事务性删+插，自动注入 org_id
- `erp_sync_service.py` — `fetch_pages_streaming()` 逐页拉取，`_get_sync_handler()` 路由
- `config.py` — `erp_sync_interval=60`, `erp_sync_shard_days=1` 等配置

### 可复用模块
- `_build_order_rows()` — 构建订单行，对账补漏直接复用
- `upsert_document_items()` — 事务写入，已有的 org_id 隔离 + 冲突更新
- `KuaiMaiClient.request()` — API 调用 + Token 自动刷新 + 网络重试
- `ErpSyncScheduler` 的 `SPECIAL_TYPES` + `_is_interval_due()` 调度机制

### 设计约束
- 必须兼容多租户 org_id 隔离
- 不能影响日常增量同步的调度节奏
- API 限流共享全局 `_API_SEM`（12QPS）

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增 `order_reconcile` 同步类型 | `erp_sync_scheduler.py` | `PRIORITY_WEIGHTS` + `SPECIAL_TYPES` + `_get_due_types()` 时间控制 |
| 新增 reconcile handler | `erp_sync_handlers.py` | 新函数 `reconcile_order()` |
| 路由注册 | `erp_sync_service.py` | `_get_sync_handler()` 添加映射 |
| Worker 路由 | `erp_sync_worker_pool.py` | 无需改动（走 `_run_sync()` 通用路径） |
| 配置项 | `config.py` | 新增 `erp_reconcile_interval` + `erp_reconcile_hour` + `erp_reconcile_tolerance` |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 对账期间增量同步并发写入 | upsert 天然幂等，冲突时 DO UPDATE，不会丢数据 | persistence |
| API 返回 total 不稳定（翻页期间有新单） | COUNT 对账允许 ±5 的误差阈值，超过才拉明细 | handler |
| 某时段 ERP API 超时 | tenacity 重试 3 次，单时段失败不阻塞其他时段 | client |
| 日均单量从1万涨到5万 | 2小时固定分片，翻页自适应，无硬编码上限 | handler |
| 对账发现大量差异（如接口切换场景） | 日志记录差异数量，正常补入，不告警 | handler |
| 多企业并发对账 | 共享 worker pool + org 并发限制（max=3），自然排队 | scheduler/worker |
| 凌晨3点服务重启 | 分布式锁 + Redis 队列，重启后自动恢复调度 | scheduler |

---

## 3. 技术栈

- 后端：Python 3.12 + FastAPI（现有）
- 数据库：PostgreSQL（现有 `erp_document_items` 表）
- 队列：Redis Sorted Set（现有 `erp_tasks`）
- 无新增依赖

---

## 4. 目录结构

### 修改文件
- `backend/services/kuaimai/erp_sync_handlers.py` — 新增 `reconcile_order()` 函数
- `backend/services/kuaimai/erp_sync_service.py` — `_get_sync_handler()` 注册新类型
- `backend/services/kuaimai/erp_sync_scheduler.py` — 注册 `order_reconcile` + 时间控制
- `backend/core/config.py` — 新增配置项

### 无新增文件
逻辑嵌入现有模块，不新建文件。

---

## 5. 核心算法：分层对账

```
reconcile_order(svc, start, end):

    第一层：COUNT 对账（12次API + 12次SQL）

    for 每个2小时时段:
      erp_count = API(pageSize=20).total
      db_count  = COUNT(DISTINCT doc_id)
      if |差异| <= tolerance(5): 跳过
      else: 标记为"需拉明细"

                   │ 只有差异时段
                   ▼

    第二层：差异时段 SID 比对

    db_sids  = SELECT DISTINCT doc_id
    erp_sids = 逐页拉取该时段全量 sid
    missing  = erp_sids - db_sids

                   │ 只有缺失的订单
                   ▼

    第三层：补漏写入

    for 缺失订单:
      rows = _build_order_rows(doc, svc)
      upsert_document_items(db, rows, org_id)
    run_aggregation(affected_keys)
```

---

## 6. 配置项设计

| 配置项 | 类型 | 默认值 | 说明 |
|-------|------|--------|------|
| `erp_reconcile_interval` | int | 86400 | 对账间隔（秒），默认24小时 |
| `erp_reconcile_hour` | int | 3 | 对账触发时间（0-23），默认凌晨3点 |
| `erp_reconcile_tolerance` | int | 5 | COUNT 对账容差，≤此值视为一致 |

---

## 7. 开发任务拆分

### 阶段1：核心对账逻辑
- [ ] 任务1.1：`config.py` 新增 3 个配置项
- [ ] 任务1.2：`erp_sync_handlers.py` 实现 `reconcile_order()` — 分层对账+补漏+聚合
- [ ] 任务1.3：`erp_sync_service.py` 的 `_get_sync_handler()` 注册 `order_reconcile`

### 阶段2：调度集成
- [ ] 任务2.1：`erp_sync_scheduler.py` — `PRIORITY_WEIGHTS` + `SPECIAL_TYPES` 注册，`_get_due_types()` 增加凌晨时间窗判断

### 阶段3：测试
- [ ] 任务3.1：单元测试 — `reconcile_order` 的分层逻辑（mock API 返回）
- [ ] 任务3.2：手动触发验证 — 用脚本直接调用 `reconcile_order()` 验证补漏效果

---

## 8. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 凌晨对账与增量同步并发 | 低 | upsert 幂等，不会冲突 |
| API 调用量增加 | 低 | 最好情况12次COUNT就结束，最差也就50+次翻页 |
| 对账时间过长阻塞 worker | 低 | 共享 worker pool，有并发限制，不会独占 |

---

## 9. 文档更新清单
- [ ] `FUNCTION_INDEX.md` — 新增 `reconcile_order` 函数
- [ ] `docs/document/TECH_ERP数据本地索引系统.md` — 补充对账机制章节

---

**状态**：待确认开发
**日期**：2026-04-02

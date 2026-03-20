## 技术设计：ERP数据本地索引系统

### 背景

快麦ERP API **不支持按商品编码查询**采购单、售后单、收货单、上架单、**销售订单**。用户问"CMSYRJTZS01什么时候到货"、"售后率怎么样"或"这个商品最近卖了多少单"时，AI无法直接通过API获取答案。

**订单特殊性**：销售出库查询（`erp.trade.list.query` / `erp.trade.outstock.simple.query`）支持所有平台（含淘宝/拼多多），返回结果的子订单 `orders[]` 包含商品编码（`sysOuterId`/`outerSkuId`），但**入参不支持按商品编码过滤**。淘系/拼多多仅不返回敏感信息（收件人/电话/地址/买家留言），业务数据完整。

**解决方案**：将采购/收货/上架/售后/**订单**/**商品主数据**全量增量同步到本地数据库，建立 `商品编码 → 单据` 的映射索引。**所有查询走本地 PostgreSQL，API 带宽全部用于后台同步**。

**API限流**：快麦 API 限制 **15 请求/秒**（per appKey，不区分接口）。后台增量同步每轮仅需 15-21 次 API 调用（~3-4 秒），远低于限额。API 带宽全部专用于同步，查询零消耗。

**数据架构**：三层设计（热数据 + 聚合层 + 冷归档）+ 商品主数据层（含货主shipper/备注/价格/重量）+ 库存快照层 + 供应商主数据层 + 平台映射层（下架检查），100 人并发查询无压力（纯本地 DB）。同步频率 ~1 分钟/轮（平台映射6小时），数据延迟 ≤1 分钟，等同实时。采购全链路：采购单→收货→上架→采退，完整闭环。

---

### 1. 现有代码分析

**已阅读文件**：

| 文件 | 行数 | 关键理解 |
|------|------|---------|
| `background_task_worker.py` | 441 | 异步轮询架构，`asyncio.Lock` 防重叠，定时任务模式（`_last_xxx` 时间戳节流） |
| `client.py` | 377 | `request_with_retry()` 带 token 自刷新，tenacity 3次重试指数退避 |
| `dispatcher.py` | 340 | `_fetch_all_pages()` 翻页终止逻辑（`返回数 < pageSize`），4000字符截断 |
| `registry/purchase.py` | 530 | 采购/收货/上架 API 定义，list 不含商品编码，detail 含 `outerId`/`itemOuterId` |
| `registry/aftersales.py` | 341 | 售后 list 直接含 `items[].mainOuterId`/`outerId`，无需二次请求 |
| `registry/trade.py` | 689 | 订单/出库 API 定义，list 返回 `orders[].sysOuterId`/`outerSkuId`，无需二次请求 |
| `formatters/trade.py` | 249 | 订单格式化，子订单含 `sysOuterId`(编码)/`outerSkuId`(SKU编码)/`num`/`price` |
| `code_identifier.py` | 495 | `_identify_product()` 先 `item.single.get` 再 `sku.get`，可扩展查本地索引 |
| `agent_loop_v2.py` | 396 | Phase1 意图路由 → Phase2 工具循环（最多8轮），erp 域工具动态加载 |
| `config.py` | 244 | Supabase/Redis/KuaiMai 配置集中管理，pydantic-settings 环境变量驱动 |

**可复用模块**：
- `BackgroundTaskWorker` 的定时任务模式 → 同步调度直接复用
- `KuaiMaiClient.request_with_retry()` → 数据拉取直接复用
- `dispatcher._fetch_all_pages()` → 全量翻页逻辑参考
- `ApiEntry` 注册表 → 新增本地索引查询工具

**设计约束**：
- 必须兼容现有 Agent Loop V2 Phase2 工具循环
- 同步服务不能阻塞现有轮询任务
- Supabase 免费版有连接数限制（需控制并发）

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增独立同步 worker | `main.py` | lifespan 中启动 `ErpSyncWorker` 独立 async task |
| 归档任务（低频） | `background_task_worker.py` | 添加 `_run_erp_archive()` 每日凌晨调用 |
| 新增本地查询工具（8个） | `erp_tools.py` | 注册 `local_purchase_query`(含采退)/`local_aftersale_query`/`local_order_query`/`local_product_stats`/`local_product_flow`(含采退)/`local_stock_query`/`local_product_identify`/`local_platform_map_query` |
| 新增同步配置项 | `config.py` | 添加 `erp_sync_*` 配置字段 |
| `code_identifier` 改造 | `code_identifier.py` | 查询工具不再依赖 API 识别；`erp_identify` 改为本地 DB 优先+API 补充 |
| Phase2 提示词更新 | `prompts/erp_prompt.py` | 告知 Brain 可用本地索引查询工具 |

---

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| **API token 过期** | `request_with_retry` 已有自动刷新，同步任务复用 | `client.py` |
| **API 超时/网络异常** | tenacity 3次重试；单次失败记录 `sync_state.last_error`，不中断整体同步 | `erp_sync_service.py` |
| **采购单详情拉取失败** | 记录失败的 `purchase_id`，下轮重试；list 数据仍正常入库 | `erp_sync_service.py` |
| **日期格式差异** | 采购/采退用 `yyyy-MM-dd HH:mm:ss`，售后/收货/上架用 `YYYY-MM-DD`，按 API 要求分别处理 | `erp_sync_service.py` |
| **数据量极大（首次全量）** | 按天分片拉取（每次跨度≤7天），避免单次超时；进度记录到 `sync_state` | `erp_sync_service.py` |
| **同步与查询并发** | PostgreSQL MVCC 天然支持读写并发；写入用 `upsert`（ON CONFLICT UPDATE） | 数据库层 |
| **重复数据** | 以 `(doc_type, doc_id, item_index)` 为唯一键，`upsert` 覆盖。item_index 入库前按确定性字段排序保证稳定 | 数据库层 |
| **商品编码变更** | 增量同步时全量覆盖明细行，旧编码自然被新数据替代 | 同步逻辑 |
| **三个月前采购单** | `purchase.order.query` 只查3个月内；`purchase_order_history` 查历史；首次全量用历史API | 同步逻辑 |
| **三个月前采退单** | `purchase.return.list.query` 只查3个月内；`purchase_return_history` 查历史；首次全量用历史API | 同步逻辑 |
| **同步任务重叠（单进程）** | `asyncio.Lock` 防止同一类型同步并行执行 | `erp_sync_service.py` |
| **多Worker重复同步** | Redis 分布式锁（`SET NX EX`），4个 uvicorn worker 只有1个执行同步，其余跳过 | `erp_sync_service.py` |
| **Supabase 连接数** | 同步使用单连接串行执行，不开并发连接池 | 数据库层 |
| **Redis 不可用** | 同步状态存 Supabase（持久化），Redis 仅做可选缓存加速。**分布式锁降级**：Redis 连接失败时降级为 DB 锁（查 sync_state.status + last_run_at），upsert 幂等兜底 | 架构设计 |
| **归档期间查询** | 归档分批执行（每批1000条），不锁表；PostgreSQL MVCC 保证读不受影响 | 归档任务 |
| **聚合数据重算** | upsert 模式，同一天多次同步会覆盖更新聚合值，幂等安全 | 聚合计算 |
| **热表数据量膨胀** | 3个月归档兜底；若业务量激增可调短归档周期（配置化） | 归档策略 |
| **订单量大（日均1万单）** | 用 `time_type=upd_time` 增量同步，每小时拉取变更订单（~1000-2000单/次，~10次API调用），频率越高单次量越小 | `erp_sync_service.py` |
| **订单状态持续变化** | `upd_time` 捕获所有状态变更（退款/发货/审核/挂起），upsert 自动更新本地状态 | 同步逻辑 |
| **淘系/拼多多敏感信息** | API 不返回收件人/电话/地址/买家留言/发票信息，本地也不存；业务数据（金额/商品/状态/物流）完整 | 数据模型 |
| **订单首次全量回填量大** | 90天 × 10,000单 = 90万单，按天分片 pageSize=200，约4,500次API调用，~25分钟完成（受 DB upsert 瓶颈 ~3 req/s） | `erp_sync_service.py` |
| **本地查询无数据** | 返回"未找到相关记录"即可；数据延迟 ≤1 分钟，无需触发按需增量 | `erp_local_query.py` |
| **商品主数据未同步** | 首次启动时全量同步商品目录（~10分钟），完成前查询返回"商品同步中，请稍后" | `erp_sync_service.py` |
| **商品编码变更/合并** | 增量同步按 `outer_id` upsert 覆盖，历史单据中旧编码自然保留 | 同步逻辑 |
| **库存高频变动** | 库存每分钟同步，出入库密集时单轮变更可达 200+ 条，翻页处理（pageSize=50, 最多4页） | `erp_sync_service.py` |
| **售后工单 items[] 为空** | 部分工单（仅退款 type=1,5）可能无商品明细。仍插入一行（outer_id=NULL, item_index=0），记录工单级信息（退款金额/原因/店铺/tid）。查询"商品X的售后"用 `WHERE outer_id='X'` 自动排除；查询"全部售后统计"包含此类工单 | `erp_sync_service.py` |
| **商品被删除（非停用）** | API 增量同步不返回已删除商品。每日凌晨全量拉商品目录对比，不在列表中的标记 `active_status=-1`（已删除）。历史单据 outer_id 不受影响，identify 工具提示"此商品已删除" | `erp_sync_service.py` |
| **增量时间窗口过大（宕机恢复）** | 若 `end - start > 7天`（服务长时间宕机后重启），自动切换为按7天分片模式，逐片执行，每片完成后更新 sync_state | `erp_sync_service.py` |

---

### 3. 技术栈

- **后端**：Python 3.x + FastAPI（现有）
- **数据库**：Supabase (PostgreSQL)（现有）
- **缓存**：Redis（现有，可选加速）
- **定时调度**：BackgroundTaskWorker 扩展（现有）
- **API客户端**：KuaiMaiClient（现有）
- **无新增依赖**

---

### 4. 目录结构

#### 新增文件

```
backend/services/kuaimai/
├── erp_sync_worker.py        # 独立同步 Worker（独立 async task，不阻塞现有轮询）
├── erp_sync_service.py       # 同步服务主逻辑（拉取+入库+状态管理+聚合计算）
├── erp_local_query.py        # 本地索引查询接口（供AI工具调用）
├── erp_stats_query.py        # 聚合统计查询接口（供报表/分析用）
```

#### 修改文件

```
backend/main.py                               # lifespan 启动 ErpSyncWorker 独立 task
backend/services/background_task_worker.py     # 仅添加归档任务（低频，每日凌晨）
backend/core/config.py                         # 添加同步配置项
backend/services/kuaimai/erp_tools.py          # 注册本地查询工具定义
backend/services/kuaimai/code_identifier.py    # erp_identify 改为本地DB优先+API补充
backend/services/kuaimai/prompts/erp_prompt.py # 更新Phase2提示词
```

---

### 5. 数据库设计

#### 三层数据架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         数据写入流（API 全部用于同步）                │
│                                                                  │
│  快麦API ──同步──→ erp_products / erp_product_skus (商品主数据)     │
│           ──同步──→ erp_suppliers (供应商主数据)                    │
│           ──同步──→ erp_stock_status (库存快照)                    │
│           ──同步──→ erp_product_platform_map (平台映射，低频)       │
│           ──同步──→ erp_document_items (热) ──聚合──→ erp_product_daily_stats │
│                          │                                       │
│                     3个月后归档                                    │
│                          ↓                                       │
│                  erp_document_items_archive (冷)                  │
└──────────────────────────────────────────────────────────────────┘

查询路径（全部走本地 PostgreSQL，零 API 消耗）：
  商品识别   → erp_products + erp_product_skus（商品名/类型/SKU/条码/货主shipper/备注/价格/图片URL）
  商品名搜索 → erp_products.title pg_trgm GIN索引（ILIKE中文子串模糊搜索）
  规格名搜索 → erp_product_skus.properties_name pg_trgm GIN索引（ILIKE中文子串模糊搜索）
  供应商查询 → erp_suppliers（供应商名/编码/联系人/分类/交期）
  库存查询   → erp_stock_status（总库存/可售/锁定/在途/预警）
  下架检查   → erp_product_platform_map（ERP编码↔平台商品ID↔店铺映射）
  单据明细   → erp_document_items 热表（近3个月，含备注/创建人/买家留言）
  报表分析   → erp_product_daily_stats 聚合表（永久保留，毫秒级）
  历史追溯   → erp_document_items_archive 冷表（按需查询）
```

#### 表1：`erp_document_items`（热数据 — 近3个月明细）

统一存储所有单据类型的商品明细行，通过 `doc_type` 区分。AI 工具直接查此表。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | BIGSERIAL | PK | auto | 自增主键 |
| doc_type | VARCHAR(20) | NOT NULL | - | 单据类型：purchase/receipt/shelf/aftersale/**order**/**purchase_return** |
| doc_id | VARCHAR(64) | NOT NULL | - | 单据ID（采购单ID/收货单ID/上架单ID/工单ID） |
| doc_code | VARCHAR(64) | - | - | 单据编号（如采购单号 DB20260315001） |
| doc_status | VARCHAR(32) | - | - | 单据状态（GOODS_NOT_ARRIVED/FINISHED 等） |
| doc_created_at | TIMESTAMP | - | - | 单据创建时间 |
| doc_modified_at | TIMESTAMP | - | - | 单据最后修改时间 |
| item_index | SMALLINT | NOT NULL | 0 | 明细行序号（同一单据内） |
| outer_id | VARCHAR(128) | - | - | 主商家编码（SPU级） |
| sku_outer_id | VARCHAR(128) | - | - | SKU商家编码 |
| item_name | VARCHAR(256) | - | - | 商品名称 |
| quantity | DECIMAL(12,2) | - | - | 数量（采购数/收货数/售后数/采退数） |
| quantity_received | DECIMAL(12,2) | - | - | 已到货数量（仅采购单） |
| price | DECIMAL(12,2) | - | - | 单价 |
| amount | DECIMAL(12,2) | - | - | 金额 |
| supplier_name | VARCHAR(128) | - | - | 供应商名称（采购/收货/采退） |
| warehouse_name | VARCHAR(128) | - | - | 仓库名称（采购/收货/上架/订单/采退均可用） |
| shop_name | VARCHAR(128) | - | - | 店铺名称（售后/订单） |
| platform | VARCHAR(20) | - | - | 来源平台（订单/售后：tb/jd/pdd/dy/xhs/1688等） |
| order_no | VARCHAR(64) | - | - | 平台订单号tid（订单+售后共用；售后←tid，关联原订单的桥梁） |
| order_status | VARCHAR(32) | - | - | 订单系统状态（仅订单：WAIT_AUDIT/SELLER_SEND_GOODS/FINISHED等） |
| express_no | VARCHAR(64) | - | - | 快递单号（仅订单） |
| express_company | VARCHAR(64) | - | - | 快递公司（仅订单，如：圆通/中通/顺丰） |
| cost | DECIMAL(12,2) | - | - | 成本价（仅订单子商品，来自 orders[].cost） |
| pay_time | TIMESTAMP | - | - | 支付时间（仅订单，来自 payTime） |
| consign_time | TIMESTAMP | - | - | 发货时间（仅订单，来自 consignTime） |
| refund_status | VARCHAR(32) | - | - | 退款状态（仅订单子商品，来自 orders[].refundStatus） |
| discount_fee | DECIMAL(12,2) | - | - | 折扣金额（仅订单，订单级 discountFee 按子商品 payment 比例均摊）。⚠ **尾差兜底**：最后一个子商品的 discount_fee = 总折扣 - 前 N-1 个子商品折扣之和，避免浮点精度导致均摊总和 ≠ 原始值 |
| post_fee | DECIMAL(12,2) | - | - | 运费（仅订单，← postFee）。⚠ **仅 item_index=0 的首行存值，其余行 NULL**——订单级字段，若每行都存则 SUM 会重复计算 N 倍 |
| gross_profit | DECIMAL(12,2) | - | - | 毛利（仅订单，← grossProfit）。⚠ **仅 item_index=0 的首行存值，其余行 NULL**——订单级字段，聚合层使用 `SUM(amount) - SUM(cost * quantity)` 按子商品级计算毛利，不依赖此字段 SUM。此字段仅用于单笔订单展示时的参考值 |
| aftersale_type | SMALLINT | - | - | 售后类型（仅售后：0=其他,1=已发货仅退款,2=退货,3=补发,4=换货,5=未发货仅退款,7=拒收退货,8=档口退货,9=维修；← afterSaleType） |
| refund_money | DECIMAL(12,2) | - | - | 系统退款金额（仅售后，← refundMoney） |
| raw_refund_money | DECIMAL(12,2) | - | - | 平台实退金额（仅售后，← rawRefundMoney） |
| text_reason | VARCHAR(256) | - | - | 售后原因（仅售后，← textReason） |
| finished_at | TIMESTAMP | - | - | 完结时间（仅售后，← finished；处理时效 = finished_at - doc_created_at） |
| real_qty | DECIMAL(12,2) | - | - | 实退数量（仅售后商品，← itemRealQty；对比 quantity 申请数） |
| delivery_date | TIMESTAMP | - | - | 交货日期（仅采购，← deliveryDate；交期跟踪） |
| actual_return_qty | DECIMAL(12,2) | - | - | 实退数量（仅采退，← actualReturnNum；对比 quantity 申请退货数） |
| purchase_order_code | VARCHAR(64) | - | - | 关联采购单号（收货/采退，← purchaseOrderCode/purchaseOrderId；收货→采购追溯） |
| remark | TEXT | - | - | 单据/行备注（采购/售后 ← `remark`；订单 ← `sellerMemo` 卖家备注） |
| sys_memo | TEXT | - | - | 系统备注（仅订单，← `sysMemo`） |
| buyer_message | TEXT | - | - | 买家留言（仅订单，← `buyerMessage`） |
| creator_name | VARCHAR(64) | - | - | 创建人（采购单/收货单/采退单 ← `createrName`） |
| extra_json | JSONB | - | '{}' | 扩展字段（见下方 extra_json 字段清单） |
| synced_at | TIMESTAMP | NOT NULL | NOW() | 同步时间 |

**extra_json 各类型存储字段**：
- **采购**: shortId(短号), totalAmount(总金额), actualTotalAmount(实际金额), financeStatus(财务状态), arrivedQuantity(单据级已到货), receiveQuantity(单据级已收货), totalFee(调后行金额), amendAmount(调整金额)
- **售后**: goodStatus(货物状态1-4), refundWarehouseName(退货仓库), refundExpressCompany/refundExpressId(退回快递), reissueSid(补发单号), platformId(平台售后单号), shortId, payment(商品级实付)
- **订单**: type(订单类型), payAmount(实付总额), isCancel/isRefund/isExcep/isHalt/isUrgent(标记), payment(子商品实付)
- **收货**: shelvedQuantity(已上架数), getGoodNum/getBadNum(良品/次品数), totalDetailFee(总金额), busyTypeDesc(业务类型)
- **采退**: shortId(短号), totalAmount(总金额), financeStatus(财务状态), statusName(状态名), tagName(标签)

**索引**：
- `idx_doc_items_outer_id`：`(outer_id)` — 按主商家编码查询
- `idx_doc_items_sku_outer_id`：`(sku_outer_id)` — 按SKU编码查询
- `idx_doc_items_doc_type_outer`：`(doc_type, outer_id, doc_created_at DESC)` — 按类型+编码+日期范围查询（核心查询路径，几乎所有工具都走此索引。三列复合避免对高频商品做大量 table lookup）
- `idx_doc_items_doc_type_sku`：`(doc_type, sku_outer_id, doc_created_at DESC)` — 按类型+SKU编码+日期范围查询
- `idx_doc_items_doc_id`：`(doc_type, doc_id)` — 按单据ID查询（防重复）
- `idx_doc_items_modified`：`(doc_modified_at)` — 归档任务按时间筛选
- `idx_doc_items_platform`：`(platform)` WHERE platform IS NOT NULL — 按平台过滤（订单/售后）
- `idx_doc_items_shop`：`(shop_name, doc_type)` — 按店铺+类型查询
- `idx_doc_items_order_no`：`(order_no)` WHERE order_no IS NOT NULL — 按平台订单号查（订单/售后共用）
- `idx_doc_items_refund`：`(refund_status)` WHERE refund_status IS NOT NULL — 退款状态筛选（订单专用）
- `idx_doc_items_consign`：`(consign_time)` WHERE consign_time IS NOT NULL — 按发货时间查（物流时效分析）
- `idx_doc_items_aftersale_type`：`(aftersale_type)` WHERE aftersale_type IS NOT NULL — 按售后类型筛选
- `idx_doc_items_finished`：`(finished_at)` WHERE finished_at IS NOT NULL — 售后完结时间（处理时效分析）
- `uq_doc_items`：`UNIQUE(doc_type, doc_id, item_index)` — 唯一约束（upsert 依赖）。⚠ **item_index 稳定性要求**：API 返回的子项数组（订单 orders[]、售后 items[]、采购/采退 detail items[]）排序不保证跨调用稳定。**实现时必须**：入库前按确定性字段排序后再分配 item_index——订单按 `oid` 升序，售后按 `mainOuterId+outerId` 升序，采购/采退按 `outerId+itemOuterId` 升序。确保同一单据多次同步产生一致的 item_index

#### 表2：`erp_document_items_archive`（冷归档 — 3个月前明细）

与热表 **完全相同的表结构**，存放归档数据。历史追溯时按需查询。

**索引**：与热表相同（`outer_id`、`sku_outer_id`、`doc_type` 组合索引）。
- ⚠ **必须包含唯一约束** `UNIQUE(doc_type, doc_id, item_index)` — 与热表一致。防止归档中断后重试导致冷表重复数据（见归档流程事务性要求）。

#### 表3：`erp_product_daily_stats`（聚合层 — 每日单品统计，永久保留）

每日同步完成后自动聚合计算。报表/分析功能直接查此表，不需要扫描明细。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | BIGSERIAL | PK | auto | 自增主键 |
| stat_date | DATE | NOT NULL | - | 统计日期（**= doc_created_at::date，即单据创建日期**）。⚠ 所有指标按创建日期归属同一行：3月1日创建的订单不论何时发货/退款，其 order_count/order_shipped_count/order_refund_count 都在 3月1日这行。这保证同一行内分子分母同基数（如发货率 = shipped/count 有意义）。增量聚合逻辑：本轮变更的记录按其 doc_created_at::date 找到 daily_stats 对应行，全量重算该行各指标 |
| outer_id | VARCHAR(128) | NOT NULL | - | 主商家编码 |
| sku_outer_id | VARCHAR(128) | - | - | SKU编码（NULL=SPU级汇总） |
| item_name | VARCHAR(256) | - | - | 商品名称（冗余，方便展示） |
| purchase_count | INTEGER | NOT NULL | 0 | 当日采购单数 |
| purchase_qty | DECIMAL(12,2) | NOT NULL | 0 | 当日采购数量 |
| purchase_received_qty | DECIMAL(12,2) | NOT NULL | 0 | 当日到货数量 |
| purchase_amount | DECIMAL(12,2) | NOT NULL | 0 | 当日采购金额 |
| receipt_count | INTEGER | NOT NULL | 0 | 当日收货单数 |
| receipt_qty | DECIMAL(12,2) | NOT NULL | 0 | 当日收货数量 |
| shelf_count | INTEGER | NOT NULL | 0 | 当日上架单数 |
| shelf_qty | DECIMAL(12,2) | NOT NULL | 0 | 当日上架数量 |
| purchase_return_count | INTEGER | NOT NULL | 0 | 当日采退单数 |
| purchase_return_qty | DECIMAL(12,2) | NOT NULL | 0 | 当日采退数量 |
| purchase_return_amount | DECIMAL(12,2) | NOT NULL | 0 | 当日采退金额 |
| aftersale_count | INTEGER | NOT NULL | 0 | 当日售后工单数（⚠ 仅含有商品明细的工单；仅退款 type=1,5 若 items 为空则 outer_id=NULL，无法按商品聚合。此类工单通过热表 `WHERE doc_type='aftersale' AND outer_id IS NULL` 单独统计） |
| aftersale_refund_count | INTEGER | NOT NULL | 0 | 仅退款笔数 |
| aftersale_return_count | INTEGER | NOT NULL | 0 | 退货笔数 |
| aftersale_exchange_count | INTEGER | NOT NULL | 0 | 换货笔数 |
| aftersale_reissue_count | INTEGER | NOT NULL | 0 | 补发笔数 |
| aftersale_reject_count | INTEGER | NOT NULL | 0 | 拒收退货笔数（type=7） |
| aftersale_repair_count | INTEGER | NOT NULL | 0 | 维修笔数（type=9） |
| aftersale_other_count | INTEGER | NOT NULL | 0 | 其他售后笔数（type=0,8 档口等） |
| aftersale_qty | DECIMAL(12,2) | NOT NULL | 0 | 售后商品数量 |
| aftersale_amount | DECIMAL(12,2) | NOT NULL | 0 | 售后金额 |
| order_count | INTEGER | NOT NULL | 0 | 当日销售订单数（含该商品的订单） |
| order_qty | DECIMAL(12,2) | NOT NULL | 0 | 当日销售数量 |
| order_amount | DECIMAL(12,2) | NOT NULL | 0 | 当日销售金额 |
| order_shipped_count | INTEGER | NOT NULL | 0 | 当日已发货订单数 |
| order_finished_count | INTEGER | NOT NULL | 0 | 当日已完成订单数 |
| order_refund_count | INTEGER | NOT NULL | 0 | 当日退款订单数（含子商品 refundStatus 非空） |
| order_cancelled_count | INTEGER | NOT NULL | 0 | 当日取消订单数（isCancel=1） |
| order_cost | DECIMAL(12,2) | NOT NULL | 0 | 当日销售成本（由 `SUM(cost * quantity)` 子商品级汇总，毛利 = order_amount - order_cost。⚠ 不使用 gross_profit 字段聚合，因其为订单级值仅存首行） |
| updated_at | TIMESTAMP | NOT NULL | NOW() | 最后更新时间 |

**⚠ 聚合计算规则（实现必读）**：

热表 `erp_document_items` 以**子商品粒度**存储（一个订单含3个子商品 = 3行，同一采购单含2个SKU = 2行）。聚合到 `daily_stats` 时 `*_count` 必须按单据去重，否则严重超算：

| 聚合类型 | 字段 | SQL 模式 | 说明 |
|---------|------|---------|------|
| **单数统计** | `*_count` | `COUNT(DISTINCT doc_id)` | 按单据去重。1个订单含同商品2个SKU仍算1笔 |
| **数量汇总** | `*_qty` | `SUM(quantity)` | 所有行数量求和 |
| **金额汇总** | `*_amount` / `order_cost` | `SUM(amount)` / `SUM(cost * quantity)` | 子商品级求和 |
| **已发货** | `order_shipped_count` | `COUNT(DISTINCT doc_id) FILTER(WHERE consign_time IS NOT NULL)` | 有发货时间=已发货 |
| **已完成** | `order_finished_count` | `COUNT(DISTINCT doc_id) FILTER(WHERE order_status = 'FINISHED')` | — |
| **退款** | `order_refund_count` | `COUNT(DISTINCT doc_id) FILTER(WHERE refund_status IS NOT NULL)` | 子商品级退款状态，DISTINCT 保证一单只计一次 |
| **取消** | `order_cancelled_count` | `COUNT(DISTINCT doc_id) FILTER(WHERE (extra_json->>'isCancel')::int = 1)` | isCancel 存于 extra_json，每行共享订单级标记 |

**SPU 级聚合**（sku_outer_id=NULL 的行）：对同一 outer_id 的所有 SKU 行做上述聚合，一个订单含同商品2个不同 SKU 仍算 `order_count=1`。

**索引**：
- `uq_daily_stats`：`UNIQUE(stat_date, outer_id, COALESCE(sku_outer_id, ''))` — 每日+编码唯一（upsert 依赖）。⚠ 必须用 COALESCE：PostgreSQL UNIQUE 中 NULL≠NULL，若用原始 sku_outer_id 则 SPU 级汇总行（sku_outer_id=NULL）可无限插入重复行，upsert ON CONFLICT 永远不触发
- `idx_daily_stats_outer`：`(outer_id, stat_date DESC)` — 按编码查历史趋势
- `idx_daily_stats_date`：`(stat_date)` — 按日期范围聚合

**查询示例**：

月度售后率（秒级返回）：
```sql
SELECT outer_id, item_name,
       SUM(aftersale_count) as total_aftersale,
       SUM(aftersale_amount) as total_aftersale_amount
FROM erp_product_daily_stats
WHERE stat_date BETWEEN '2026-03-01' AND '2026-03-31'
GROUP BY outer_id, item_name
ORDER BY total_aftersale DESC;
```

月度商品销量排行（秒级返回）：
```sql
SELECT outer_id, item_name,
       SUM(order_count) as total_orders,
       SUM(order_qty) as total_qty,
       SUM(order_amount) as total_amount
FROM erp_product_daily_stats
WHERE stat_date BETWEEN '2026-03-01' AND '2026-03-31'
GROUP BY outer_id, item_name
ORDER BY total_qty DESC;
```

月度毛利分析（秒级返回）：
```sql
SELECT outer_id, item_name,
       SUM(order_amount) as revenue,
       SUM(order_cost) as cost,
       SUM(order_amount) - SUM(order_cost) as gross_profit,
       ROUND((SUM(order_amount) - SUM(order_cost)) / NULLIF(SUM(order_amount), 0) * 100, 1) as margin_pct
FROM erp_product_daily_stats
WHERE stat_date BETWEEN '2026-03-01' AND '2026-03-31'
GROUP BY outer_id, item_name
ORDER BY gross_profit DESC;
```

退款率分析（秒级返回）：
```sql
SELECT outer_id, item_name,
       SUM(order_count) as total_orders,
       SUM(order_refund_count) as refund_orders,
       SUM(order_cancelled_count) as cancelled_orders,
       ROUND(SUM(order_refund_count)::numeric / NULLIF(SUM(order_count), 0) * 100, 1) as refund_rate_pct
FROM erp_product_daily_stats
WHERE stat_date BETWEEN '2026-03-01' AND '2026-03-31'
GROUP BY outer_id, item_name
ORDER BY refund_rate_pct DESC;
```

发货时效分析（从热表查询）：
```sql
SELECT outer_id,
       AVG(EXTRACT(EPOCH FROM (consign_time - pay_time)) / 3600) as avg_ship_hours,
       COUNT(*) FILTER(WHERE consign_time - pay_time <= INTERVAL '24 hours') * 100.0 / COUNT(*) as ship_24h_rate
FROM erp_document_items
WHERE doc_type = 'order' AND pay_time IS NOT NULL AND consign_time IS NOT NULL
  AND pay_time >= NOW() - INTERVAL '30 days'
GROUP BY outer_id;
```

#### 表4：`erp_products`（商品主数据 — SPU级）

本地商品目录，替代 `code_identifier.py` 的 API 调用。查询工具可直接关联获取商品名、类型等信息。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | BIGSERIAL | PK | auto | 自增主键 |
| outer_id | VARCHAR(128) | UNIQUE, NOT NULL | - | 主商家编码 |
| title | VARCHAR(256) | - | - | 商品名称 |
| item_type | SMALLINT | NOT NULL | 0 | 类型：0=普通,1=SKU套件,2=纯套件,3=包材 |
| is_virtual | BOOLEAN | NOT NULL | false | 是否虚拟商品 |
| active_status | SMALLINT | NOT NULL | 1 | 状态：1=启用,0=停用 |
| barcode | VARCHAR(64) | - | - | 商品条码 |
| purchase_price | DECIMAL(12,2) | - | - | 采购价（purchasePrice） |
| selling_price | DECIMAL(12,2) | - | - | 销售价（priceOutput） |
| market_price | DECIMAL(12,2) | - | - | 市场价（marketPrice） |
| weight | DECIMAL(10,3) | - | - | 重量(Kg)，API字段 `weight`，示例：0.05 |
| unit | VARCHAR(16) | - | - | 单位（件/箱） |
| is_gift | BOOLEAN | NOT NULL | false | 是否赠品（makeGift） |
| sys_item_id | VARCHAR(64) | - | - | 系统商品ID（sysItemId） |
| brand | VARCHAR(64) | - | - | 品牌 |
| shipper | VARCHAR(128) | - | - | 货主名称（API字段 `shipper`，直接返回人名） |
| remark | TEXT | - | - | 商品备注（API 返回可能含 HTML，同步时清洗） |
| created_at | TIMESTAMP | - | - | 商品创建时间（API字段 `created`，时间戳） |
| modified_at | TIMESTAMP | - | - | 商品更新时间（API字段 `modified`，增量同步校验用） |
| pic_url | VARCHAR(512) | - | - | 商品主图URL（← picPath，供展示/拍照识别用） |
| suit_singles | JSONB | - | NULL | 套件子单品列表（仅套件类型） |
| extra_json | JSONB | - | '{}' | 扩展字段（sellerCats分类/classify类目/standard执行标准/safekind安全类别/x,y,z尺寸/boxnum箱规/customAttribute自定义属性等） |
| synced_at | TIMESTAMP | NOT NULL | NOW() | 同步时间 |

**索引**：
- `uq_products_outer_id`：`UNIQUE(outer_id)`
- `idx_products_barcode`：`(barcode)` WHERE barcode IS NOT NULL — 条码查询
- `idx_products_title`：GIN `(title gin_trgm_ops)` — 商品名称模糊搜索（需启用 `pg_trgm` 扩展，Supabase 已内置支持）。⚠ 不用 `to_tsvector('simple', ...)` 因为 simple 分词器不做中文分词，会把 `"宠物食品猫粮5kg装"` 当作一整个 token，搜索 `'猫粮'` 匹配不到。改用 `pg_trgm` 三元组索引 + `ILIKE '%猫粮%'`，对中文子串匹配效果好
- `idx_products_type`：`(item_type)` — 按类型筛选
- `idx_products_shipper`：`(shipper)` WHERE shipper IS NOT NULL — 按货主查商品

#### 表5：`erp_product_skus`（SKU明细）

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | BIGSERIAL | PK | auto | 自增主键 |
| outer_id | VARCHAR(128) | NOT NULL | - | 所属商品主编码（关联 erp_products.outer_id） |
| sku_outer_id | VARCHAR(128) | NOT NULL | - | SKU商家编码 |
| properties_name | VARCHAR(256) | - | - | 规格属性（如"颜色:红色 尺码:XL"） |
| barcode | VARCHAR(64) | - | - | SKU条码 |
| purchase_price | DECIMAL(12,2) | - | - | SKU采购价（purchasePrice） |
| selling_price | DECIMAL(12,2) | - | - | SKU销售价（priceOutput） |
| market_price | DECIMAL(12,2) | - | - | SKU市场价（marketPrice） |
| weight | DECIMAL(10,3) | - | - | 重量(Kg) |
| unit | VARCHAR(16) | - | - | 单位 |
| shipper | VARCHAR(128) | - | - | 货主名称（SKU级也有，API字段 `shipper`） |
| pic_url | VARCHAR(512) | - | - | SKU图片URL（← skuPicPath，供展示/拍照识别用） |
| sys_sku_id | VARCHAR(64) | - | - | 系统SKU ID |
| active_status | SMALLINT | NOT NULL | 1 | 状态：1=启用,0=停用 |
| extra_json | JSONB | - | '{}' | 扩展字段（skuComponent成分/skuRemark规格备注/propertiesAlias别名/x,y,z尺寸/boxnum箱规等） |
| synced_at | TIMESTAMP | NOT NULL | NOW() | 同步时间 |

**索引**：
- `uq_skus_sku_outer_id`：`UNIQUE(sku_outer_id)`
- `idx_skus_outer_id`：`(outer_id)` — 按主编码查所有 SKU
- `idx_skus_barcode`：`(barcode)` WHERE barcode IS NOT NULL
- `idx_skus_properties_name`：GIN `(properties_name gin_trgm_ops)` — 规格名称模糊搜索（pg_trgm 三元组索引，支持中文子串匹配 `ILIKE '%红色%'`）

**外键**：无硬外键（同步顺序不保证），通过 `outer_id` 逻辑关联 `erp_products`。

#### 表6：`erp_stock_status`（库存快照 — SKU级实时库存）

本地库存快照，替代 `stock.api.status.query` 的实时 API 调用。~1 分钟同步一次，等同实时。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | BIGSERIAL | PK | auto | 自增主键 |
| outer_id | VARCHAR(128) | NOT NULL | - | 主商家编码 |
| sku_outer_id | VARCHAR(128) | - | - | SKU编码（NULL=SPU级汇总行） |
| item_name | VARCHAR(256) | - | - | 商品名称（冗余，方便展示） |
| properties_name | VARCHAR(256) | - | - | 规格属性 |
| total_stock | DECIMAL(12,2) | NOT NULL | 0 | 总库存（totalAvailableStockSum） |
| sellable_num | DECIMAL(12,2) | NOT NULL | 0 | 可售数量（sellableNum） |
| available_stock | DECIMAL(12,2) | NOT NULL | 0 | 实际可用（totalAvailableStock） |
| lock_stock | DECIMAL(12,2) | NOT NULL | 0 | 锁定库存（totalLockStock） |
| purchase_num | DECIMAL(12,2) | NOT NULL | 0 | 采购在途（purchaseNum） |
| on_the_way_num | DECIMAL(12,2) | NOT NULL | 0 | 销退在途（onTheWayNum） |
| defective_stock | DECIMAL(12,2) | NOT NULL | 0 | 残次品（totalDefectiveStock） |
| virtual_stock | DECIMAL(12,2) | NOT NULL | 0 | 虚拟库存（virtualStock） |
| stock_status | SMALLINT | NOT NULL | 0 | 状态：1=正常,2=警戒,3=无货,4=超卖,6=有货 |
| purchase_price | DECIMAL(12,2) | - | - | 采购价 |
| selling_price | DECIMAL(12,2) | - | - | 销售价 |
| market_price | DECIMAL(12,2) | - | - | 市场价 |
| allocate_num | DECIMAL(12,2) | NOT NULL | 0 | 调拨在途（allocateNum） |
| refund_stock | DECIMAL(12,2) | NOT NULL | 0 | 退款库存（refundStock） |
| purchase_stock | DECIMAL(12,2) | NOT NULL | 0 | 入库暂存（purchaseStock，采购待上架） |
| supplier_codes | VARCHAR(256) | - | - | 关联供应商编码（supplierCodes，多个逗号分隔） |
| supplier_names | VARCHAR(256) | - | - | 关联供应商名称（supplierNames，多个逗号分隔） |
| warehouse_id | VARCHAR(64) | - | - | 仓库ID（wareHouseId，区分多仓库） |
| stock_modified_time | TIMESTAMP | - | - | 库存更新时间（stockModifiedTime，最后变动时间） |
| extra_json | JSONB | - | '{}' | 扩展字段（brand品牌/cidName分类/unit单位/place产地/itemBarcode条码/skuBarcode SKU条码） |
| synced_at | TIMESTAMP | NOT NULL | NOW() | 同步时间 |

**索引**：
- `uq_stock_outer_sku`：`UNIQUE(outer_id, COALESCE(sku_outer_id, ''))` — upsert 依赖
- `idx_stock_outer_id`：`(outer_id)` — 按主编码查所有 SKU 库存
- `idx_stock_sku_outer_id`：`(sku_outer_id)` WHERE sku_outer_id IS NOT NULL — 按 SKU 查
- `idx_stock_status`：`(stock_status)` — 按库存状态筛选（预警/无货/超卖）
- `idx_stock_sellable`：`(sellable_num)` — 按可售数量排序/筛选
- `idx_stock_warehouse`：`(warehouse_id)` WHERE warehouse_id IS NOT NULL — 按仓库筛选

**数据量预估**：~5,000 商品 × ~3 SKU/商品 ≈ ~15,000 行，极小。

#### 表7：`erp_suppliers`（供应商主数据）

本地供应商目录，通过 `supplier.list.query` API 全量拉取。数据量极小（通常几十到几百家），每轮全量覆盖。

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | BIGSERIAL | PK | auto | 自增主键 |
| code | VARCHAR(64) | UNIQUE, NOT NULL | - | 供应商编码 |
| name | VARCHAR(128) | NOT NULL | - | 供应商名称 |
| status | SMALLINT | NOT NULL | 1 | 1=启用,0=停用 |
| contact_name | VARCHAR(64) | - | - | 联系人 |
| mobile | VARCHAR(32) | - | - | 手机 |
| phone | VARCHAR(32) | - | - | 电话 |
| email | VARCHAR(128) | - | - | 邮箱 |
| category_name | VARCHAR(64) | - | - | 供应商分类 |
| bill_type | VARCHAR(32) | - | - | 结算方式 |
| plan_receive_day | INTEGER | - | - | 预计交期(天) |
| address | TEXT | - | - | 地址 |
| remark | TEXT | - | - | 备注 |
| synced_at | TIMESTAMP | NOT NULL | NOW() | 同步时间 |

**索引**：
- `uq_suppliers_code`：`UNIQUE(code)` — upsert 依赖
- `idx_suppliers_name`：`(name)` — 按名称查询
- `idx_suppliers_status`：`(status)` — 按启用/停用筛选

**数据量预估**：几十到几百家，极小。同步方式：全量覆盖（`fetch_all=True, page_size=500`），每轮 1 次 API 调用。

#### 表8：`erp_product_platform_map`（平台商品↔ERP编码映射）

平台商品ID与ERP编码的对应关系。**核心用途：下架检查** — 下架某平台商品前，确认影响哪些ERP商品编码；或反查某ERP编码在哪些平台/店铺有上架。数据变动低频，每6小时全量同步一次。

数据来源：`erp.item.outerid.list.get`

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | BIGSERIAL | PK | auto | 自增主键 |
| outer_id | VARCHAR(128) | NOT NULL | - | ERP主商家编码 |
| num_iid | VARCHAR(64) | NOT NULL | - | 平台商品ID（numIid） |
| user_id | VARCHAR(64) | - | - | 店铺ID（userId，通过shop_list可查店铺名） |
| title | VARCHAR(256) | - | - | 平台商品名称 |
| sku_mappings | JSONB | - | '[]' | SKU映射列表 `[{"skuOuterId":"XX01-01","skuNumIid":"123456"},...]` |
| synced_at | TIMESTAMP | NOT NULL | NOW() | 同步时间 |

**索引**：
- `uq_platform_map`：`UNIQUE(outer_id, num_iid)` — upsert 依赖
- `idx_platform_map_outer`：`(outer_id)` — 按ERP编码查（下架检查：此编码在哪些平台有售）
- `idx_platform_map_numiid`：`(num_iid)` — 按平台商品ID查（反查对应ERP编码）
- `idx_platform_map_user`：`(user_id)` — 按店铺筛选

**数据量预估**：~5,000 商品 × ~2 平台/商品 ≈ ~10,000 行，极小。同步方式：增量（`startTime/endTime`），每6小时一次。

#### 表9：`erp_sync_state`（同步状态追踪）

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | SERIAL | PK | auto | 自增主键 |
| sync_type | VARCHAR(20) | UNIQUE, NOT NULL | - | 同步类型：purchase/receipt/shelf/aftersale/order/**purchase_return**/product/stock/supplier/**platform_map**/archive/stats |
| last_sync_time | TIMESTAMP | - | - | 上次成功同步的数据截止时间 |
| last_run_at | TIMESTAMP | - | - | 上次运行时间 |
| error_count | SMALLINT | NOT NULL | 0 | 连续失败次数。每次同步成功归零，失败+1。查询层据此判断数据新鲜度（≥3 提示用户） |
| last_error | TEXT | - | - | 上次错误信息（成功时清空） |
| total_synced | INTEGER | NOT NULL | 0 | 累计同步记录数 |
| status | VARCHAR(16) | NOT NULL | 'idle' | 状态：idle/running/error |
| is_initial_done | BOOLEAN | NOT NULL | false | 首次全量同步是否已完成。⚠ **关键标记**：false 时执行全量模式（分片历史拉取），true 后切换增量模式。防止首次全量中途中断（如进程重启）后 last_sync_time 已部分更新，误入增量模式导致漏拉历史数据 |

**索引**：
- `uq_sync_type`：`UNIQUE(sync_type)` — 每种类型一行

---

### 5.1 归档策略

#### 归档规则

| 操作 | 频率 | 逻辑 |
|------|------|------|
| **热→冷迁移** | 每天凌晨1次 | `doc_modified_at < NOW() - 3个月` 的记录 INSERT INTO archive → DELETE FROM 热表 |
| **聚合计算** | 每次同步后 | 对当天变更的商品重新聚合写入 `erp_product_daily_stats`（upsert） |
| **冷数据清理** | 可选，手动 | 超过12个月的 archive 记录可按需清理（聚合数据已永久保留） |

#### 归档流程

```
每日凌晨 03:00（BackgroundTaskWorker 定时触发）：
1. 查询热表中 doc_modified_at < (NOW - 90天) 的记录 ID 列表
2. 分批（每批1000条），每批在同一事务（BEGIN...COMMIT）内执行：
   a. INSERT INTO erp_document_items_archive SELECT ... ON CONFLICT DO UPDATE（upsert 幂等）
   b. DELETE FROM erp_document_items WHERE id IN (该批 ID)
   c. COMMIT
   ⚠ 事务性保证：INSERT 和 DELETE 必须在同一事务内。若步骤 a 成功但 b 前崩溃，
   无事务保护时重启后会重复 INSERT 到 archive。archive 表的 UNIQUE 约束 + upsert
   作为第二层兜底，但不能替代事务（避免垃圾数据堆积）。
3. 更新 sync_state(sync_type='archive')
```

#### 数据量控制

| 时间 | 热表行数（预估） | 聚合表行数（预估） | 冷表行数 |
|------|---------------|-------------------|---------|
| 3个月 | ~123万（订单90万+其他33万） | ~2.7万（300 SKU × 90天） | 0 |
| 6个月 | ~123万（稳定） | ~5.4万 | ~123万 |
| 12个月 | ~123万（稳定） | ~10.8万 | ~369万 |
| 24个月 | ~123万（稳定） | ~21.6万 | ~861万 |

**数据量说明**：订单日均10,000单 × 平均3个子商品 = ~30,000行/天，90天约90万行。加上原有采购/售后等33万行，热表约123万行。PostgreSQL 百万级行+索引查询仍为毫秒级。聚合表增长缓慢（年增~10万行），报表查询毫秒级。

---

### 6. 查询架构设计

#### 6.0 纯本地查询模式

**核心原则**：API 带宽全部专用于后台同步，所有用户查询 100% 走本地 PostgreSQL。

```
                    ┌─────────────────────────┐
                    │   后台同步（独占 API）      │
                    │   ~1分钟/轮，15 req/s     │
                    │   10类数据持续增量同步       │
                    └────────┬────────────────┘
                             ↓ 写入
┌──────────────────────────────────────────────────┐
│              本地 PostgreSQL                                 │
│  ┌────────────┐ ┌──────────┐ ┌────────────┐ ┌────────────┐ ┌─────────┐ │
│  │erp_products│ │suppliers │ │stock_status│ │doc_items   │ │daily    │ │
│  │+ skus      │ │(供应商)   │ │(库存快照)   │ │(热表)      │ │_stats   │ │
│  └────────────┘ └──────────┘ └────────────┘ └────────────┘ └─────────┘ │
└────────┬──────────┬────────────┬──────────────┬────────┘
         ↑          ↑            ↑              ↑
  商品/货主查询 供应商查询  库存查询    明细/流转查询   统计/报表查询
         │                 │              │
    ┌────┴─────────────────┴──────────────┴────┐
    │        100人并发查询（毫秒级，零API消耗）     │
    └──────────────────────────────────────────┘
```

**查询路由**：

| 查询类型 | 数据源 | 延迟 | 示例 |
|---------|--------|------|------|
| 商品识别 | `erp_products` + `erp_product_skus` | <10ms | "这个编码是什么？""货主是谁？""某某货主下有哪些商品？" |
| 商品名搜索 | `erp_products.title` pg_trgm GIN索引 + ILIKE | <20ms | "叫XX的商品""包含猫粮的商品" |
| 规格名搜索 | `erp_product_skus.properties_name` pg_trgm GIN索引 + ILIKE | <20ms | "红色XL的商品""规格含120g的SKU" |
| 供应商查询 | `erp_suppliers` | <10ms | "供应商联系方式""哪些供应商在合作？" |
| 库存查询 | `erp_stock_status` | <10ms | "CMSYRJTZS01库存多少？""哪些商品缺货？" |
| 下架检查 | `erp_product_platform_map` + `erp_products` | <20ms | "这个编码在哪些平台有售？""下架XX平台会影响哪些商品？" |
| 采购/收货/上架/采退明细 | `erp_document_items` WHERE doc_type=? | <50ms | "CMSYRJTZS01到货了吗？""采退了多少？""采购单备注" |
| 售后明细 | `erp_document_items` WHERE doc_type='aftersale' | <50ms | "最近5笔售后""售后备注" |
| 订单明细 | `erp_document_items` WHERE doc_type='order' | <50ms | "这个商品最近卖了多少""买家留言" |
| 统计报表 | `erp_product_daily_stats` | <20ms | "月度售后率""毛利分析" |
| 全链路流转 | `erp_document_items` 多类型聚合 | <100ms | "采购→销售→售后全流程" |

**⚠ 明细查询时间范围限制**：热表仅保留近 3 个月数据。当用户 `days > 90` 时，超出部分的**明细**在冷表（`erp_document_items_archive`）中。处理策略：
- 工具层自动 `UNION ALL` 冷表查询，对用户透明。实现时对 days ≤ 90 走纯热表（快），days > 90 时追加冷表 UNION（稍慢但数据完整）
- 统计报表类查询（`local_product_stats`）走聚合表 `daily_stats`，永久保留，无此限制

**数据新鲜度**：后台每 ~1 分钟完成一轮全类型增量同步，数据延迟 ≤1 分钟。对 ERP 运营场景等同实时。
**⚠ 异常感知**：所有查询工具在执行前检查 `erp_sync_state` 中对应 sync_type 的健康状态。当 `error_count >= 3` 或 `last_run_at` 距今超过 5 分钟时，在查询结果末尾追加 `⚠ 数据可能未及时更新（同步异常，最后成功：{last_run_at}）`。首次全量同步未完成（`is_initial_done=false`）时，追加 `ℹ 首次数据同步进行中，部分历史数据尚未就绪`。

---

#### 工具1：`local_purchase_query` — 按商品编码查采购到货进度（含采退）

注册到 `erp_tools.py`，Agent Loop Phase2 可调用。纯本地查询。同时返回采购单和采退单数据，完整呈现采购链路。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| product_code | str | 是 | 商品编码（主商家编码或SKU编码） |
| status | str | 否 | 采购单状态过滤（GOODS_NOT_ARRIVED/GOODS_PART_ARRIVED/FINISHED） |
| include_return | bool | 否 | 是否包含采退单，默认 true |
| days | int | 否 | 查询最近N天，默认30 |

**输出示例**：
```
🔍 商品 SGQTZDBWNL01-01 采购情况（近30天）：

📦 采购单 DB20260315001（部分到货）
  - 采购数: 500，已到货: 300
  - 供应商: xxx
  - 创建时间: 2026-03-15

📦 采购单 DB20260310002（已完成）
  - 采购数: 200，已到货: 200
  - 供应商: xxx
  - 创建时间: 2026-03-10

↩️ 采退单 CT20260318001（已出库）
  - 退货数: 50，实退: 50
  - 供应商: xxx
  - 创建时间: 2026-03-18

📊 汇总：2笔采购，总采购700件，已到货500件（71.4%）；1笔采退，退货50件

```

#### 工具2：`local_aftersale_query` — 按商品编码查售后情况

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| product_code | str | 是 | 商品编码 |
| type | str | 否 | 售后类型过滤（0=其他/1=已发货仅退款/2=退货/3=补发/4=换货/5=未发货仅退款/7=拒收退货/8=档口退货/9=维修） |
| days | int | 否 | 查询最近N天，默认30 |

**输出示例**：
```
🔍 商品 CMSYRJTZS01 售后情况（近30天）：

📊 售后汇总：
  - 退货: 12笔
  - 仅退款: 5笔
  - 换货: 2笔
  - 合计: 19笔

近期售后工单（最新5笔）：
  1. 工单xxx — 退货 — 处理中 — 2026-03-19
  2. 工单xxx — 仅退款 — 已完成 — 2026-03-18
  ...

```

#### 工具3：`local_order_query` — 按商品编码查销售订单

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| product_code | str | 是 | 商品编码（主商家编码或SKU编码） |
| shop_name | str | 否 | 店铺名称过滤 |
| platform | str | 否 | 平台过滤（tb/jd/pdd/dy/xhs/1688） |
| status | str | 否 | 订单状态过滤（WAIT_AUDIT/SELLER_SEND_GOODS/FINISHED等） |
| days | int | 否 | 查询最近N天，默认30 |

**输出示例**：
```
商品 CMSYRJTZS01 销售情况（近30天）：

销售汇总：
  - 总订单数: 156笔
  - 总销量: 312件
  - 总金额: ¥24,960
  - 已发货: 142笔 | 已完成: 128笔

按平台：
  - 淘宝/天猫: 98笔 ¥15,680
  - 拼多多: 35笔 ¥4,200
  - 抖音: 23笔 ¥5,080

按店铺：
  - XX旗舰店: 98笔
  - XX拼多多店: 35笔
  - XX抖音店: 23笔

退款：8笔（退款率5.1%），取消：3笔

近期订单（最新5笔）：
  1. 订单126036803257340376 — 淘宝 — 已发货 — 圆通YT7263xxx — 2件 ¥160 — 2026-03-19
  2. 订单260319-xxx — 拼多多 — 已完成 — 中通78xxx — 1件 ¥80 — 2026-03-19
  ...

```

#### 工具4：`local_product_stats` — 按商品编码查统计数据（报表/趋势）

查聚合表 `erp_product_daily_stats`，支持月度售后率、采购到货率等分析场景。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| product_code | str | 是 | 商品编码 |
| period | str | 否 | 统计周期：day/week/month，默认 month |
| start_date | str | 否 | 起始日期，默认当月1号 |
| end_date | str | 否 | 结束日期，默认今天 |

**输出示例**：
```
商品 CMSYRJTZS01 月度统计（2026-03）：

销售：156笔，销量312件，金额¥24,960（已发货142/已完成128）
  退款：8笔（退款率5.1%），取消：3笔
  成本：¥14,976，毛利：¥9,984（毛利率40.0%）
采购：3笔，采购1200件，到货800件（到货率66.7%），金额¥36,000
收货：2笔，收货800件
上架：2笔，上架800件
采退：1笔，退供应商50件，金额¥1,500
售后：19笔（退货12/退款5/换货2），售后金额¥2,850

售后率：19/156 = 12.2%
日均销量：10.4件/天
平均发货时效：6.2小时（24h内发货率 96.8%）
对比上月：销量 +22%（上月256件），售后率 -3%（上月15.2%），毛利率 +2%

```

#### 工具5：`local_product_flow` — 按商品编码查完整流转（采购→收货→上架→销售→售后→采退）

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| product_code | str | 是 | 商品编码 |
| days | int | 否 | 查询最近N天，默认30 |

**输出示例**：
```
商品 SGQTZDBWNL01-01 全链路流转（近30天）：

采购：2笔，共700件，已到500件
收货：1笔，收货300件
上架：1笔，上架300件
销售：156笔，销量312件，金额¥24,960
售后：3笔（退货2/换货1）
采退：1笔，退供应商50件

未到货：200件（采购单 DB20260315001）
售后率：3/156 = 1.9%

```

#### 工具6：`local_stock_query` — 按商品编码查库存状态

纯本地查询，替代 `stock.api.status.query` 的 API 调用。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| product_code | str | 是 | 商品编码（主商家编码或SKU编码） |
| stock_status | str | 否 | 库存状态过滤（1=正常/2=警戒/3=无货/4=超卖/6=有货） |
| low_stock | bool | 否 | 仅显示库存预警（sellable_num < 安全库存阈值），默认 false |

**输出示例**：
```
🔍 商品 CMSYRJTZS01 库存状态：

SKU CMSYRJTZS01-01（红色）：
  可售: 156 | 总库存: 200 | 锁定: 32 | 采购在途: 500 | 状态: 正常
SKU CMSYRJTZS01-02（蓝色）：
  可售: 8 | 总库存: 15 | 锁定: 5 | 采购在途: 0 | 状态: ⚠️警戒
SKU CMSYRJTZS01-03（绿色）：
  可售: 0 | 总库存: 0 | 锁定: 0 | 采购在途: 200 | 状态: 无货

📊 汇总：总可售164件，总库存215件，总在途700件
```

#### 工具7：`local_product_identify` — 编码识别（替代 code_identifier API 调用）

纯本地查询，替代原 `code_identifier.py` 的 API 调用链路。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| code | str | 否 | 商品编码/SKU编码/条码（精确匹配） |
| name | str | 否 | 商品名称关键词（pg_trgm ILIKE 模糊搜索，支持中文子串） |
| spec | str | 否 | 规格名称关键词（pg_trgm ILIKE 模糊搜索，如"红色""120g"） |

*code、name、spec 至少传一个*

**查询逻辑**：
1. **编码模式**（code 有值）：
   - 查 `erp_products` WHERE `outer_id = code` → 命中则为主编码（SPU）
   - 查 `erp_product_skus` WHERE `sku_outer_id = code` → 命中则为 SKU 编码
   - 查 `erp_products`/`erp_product_skus` WHERE `barcode = code` → 命中则为条码
   - 查 `erp_document_items` WHERE `outer_id = code OR sku_outer_id = code` → 命中则关联单据存在
   - 均未命中 → 返回"未识别"
2. **名称搜索模式**（name 有值）：
   - 查 `erp_products` WHERE `title ILIKE '%{name}%'`（pg_trgm 三元组索引加速，支持中文子串匹配）→ 返回匹配商品列表
3. **规格搜索模式**（spec 有值）：
   - 查 `erp_product_skus` WHERE `properties_name ILIKE '%{spec}%'`（pg_trgm 索引加速）→ 返回匹配SKU列表
   - 关联 `erp_products` 获取商品名称

**输出示例（编码模式）**：
```
编码识别: CMSYRJTZS01
✓ 商品存在 | 编码类型: 主编码(outer_id)
商品类型: 普通(type=0) | 货主: 郑海鹏
名称: 宠物食品猫粮 | 条码: 6901234567890 | 采购价: ¥45.00
图片: https://img.kuaimai.com/xxx/CMSYRJTZS01.jpg
备注: 新品测试中，注意库存
SKU(3个): CMSYRJTZS01-01(红色), CMSYRJTZS01-02(蓝色), CMSYRJTZS01-03(绿色)
关联单据: 采购单3笔, 售后单5笔, 订单156笔
```

**输出示例（名称搜索模式）**：
```
搜索"猫粮"匹配到3个商品：

1. CMSYRJTZS01 — 宠物食品猫粮 | 货主: 郑海鹏
   SKU: 3个 | 图片: https://img.kuaimai.com/xxx.jpg
2. CMLB0520 — 猫粮量贩装 | 货主: 李明
   SKU: 2个 | 图片: https://img.kuaimai.com/yyy.jpg
3. CMHWML01 — 户外猫粮鸟粮 | 货主: 郑海鹏
   SKU: 1个 | 图片: https://img.kuaimai.com/zzz.jpg
```

**输出示例（规格搜索模式）**：
```
搜索规格"红色 XL"匹配到5个SKU：

1. CMSYRJTZS01-01 — 宠物食品猫粮 | 规格: 颜色:红色 尺码:XL
2. YFZS0301-03 — 运动T恤 | 规格: 红色 XL码
...
```

#### 工具8：`local_platform_map_query` — 下架检查（ERP编码↔平台商品映射）

**使用场景**：下架某平台商品前，检查此编码在哪些平台/店铺有上架；或反查某平台商品ID对应的ERP编码。

**输入参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| product_code | str | 否 | ERP商品编码（查此编码在哪些平台有售） |
| num_iid | str | 否 | 平台商品ID（反查对应ERP编码） |
| user_id | str | 否 | 店铺ID过滤（只查指定店铺） |

*product_code 和 num_iid 至少传一个*

**查询逻辑**：
1. 按 `outer_id` 查 `erp_product_platform_map` → 找到所有平台映射
2. 关联 `erp_products` 获取商品名称/状态/货主
3. 关联 shop_list 将 user_id 翻译为店铺名（可选）

**输出示例**：
```
商品 CMSYRJTZS01 平台上架情况：

商品名称: 宠物食品猫粮 | 货主: 郑海鹏 | 状态: 启用

平台映射（共3条）：
  1. 淘宝旗舰店 — 平台ID: 658712345678 — SKU映射: 3个
  2. 拼多多专营店 — 平台ID: 412398765432 — SKU映射: 3个
  3. 抖音直播店 — 平台ID: 7128456789012 — SKU映射: 2个

⚠ 下架此商品将影响 3 个店铺的商品链接！
```

---

### 7. 同步策略设计

#### 7.0 运行架构

**问题**：当前部署 4 个 uvicorn worker，每个 worker 都有独立事件循环。如果把同步塞进 `BackgroundTaskWorker`，会导致：
1. 4 个 worker 重复同步（重复拉取 + 重复写入）
2. 同步阻塞图片/视频轮询任务

**方案**：独立 async task + Redis 分布式锁

```
main.py lifespan:
  ├── BackgroundTaskWorker.start()      # 原有不变（图片/视频/一致性/归档）
  └── ErpSyncWorker.start()             # 新增，独立运行
        │
        ├── Redis 分布式锁 SET erp_sync_lock NX EX 300
        │   ├── 获取成功 → 执行同步
        │   └── 获取失败 → 跳过（其他 worker 在执行）
        │
        ├── 采购/收货/上架/采退 同步（每轮）
        ├── 售后 同步（每轮）
        ├── 订单 同步（每轮，upd_time增量）
        ├── 商品 同步（每轮，modified增量）
        ├── 库存 同步（每轮，stockModified增量）
        ├── 供应商 同步（每轮，全量覆盖，数据量极小）
        ├── 平台映射 同步（每6小时，增量，低频变动）
        ├── 聚合计算
        └── sleep 60s → 下一轮
```

**Redis 分布式锁设计**：
- Key：`erp_sync:{sync_type}:lock`（如 `erp_sync:purchase:lock`）
- TTL：同步间隔的 2 倍（如采购 30min 间隔 → 锁 60min），防止进程异常退出后锁永久占用
- 粒度：按 sync_type 分锁，不同类型可并行（采购和售后同时同步）

**⚠ Redis 不可用时的降级策略**：
Redis 锁获取可能因 Redis 宕机/网络异常而抛 ConnectionError。降级逻辑：
1. `try: acquire_lock()` → 成功则执行同步
2. `except ConnectionError:` → 降级到 DB 锁：查 `erp_sync_state` 表中目标 sync_type 的 `status` 和 `last_run_at`
   - 若 `status='running' AND last_run_at > NOW() - 2分钟` → 认为其他 worker 在执行，跳过
   - 若 `status != 'running' OR last_run_at < NOW() - 2分钟` → 用**原子 CAS 更新**抢锁：`UPDATE erp_sync_state SET status='running', last_run_at=NOW() WHERE sync_type=? AND (status != 'running' OR last_run_at < NOW() - INTERVAL '2 minutes') RETURNING id`。仅当 RETURNING 返回行时执行同步（未返回说明其他 worker 先抢到）。避免 SELECT→UPDATE 之间的 TOCTOU 竞态
3. 降级模式下多 worker 可能短暂重复同步（几秒内），但 upsert 幂等保证数据正确，仅浪费少量 API 调用
4. Redis 恢复后自动切回 Redis 锁，无需人工干预

**归档任务**保留在 `BackgroundTaskWorker` 内（每日凌晨一次，耗时 5-20s，不影响轮询）。

#### 7.1 同步方式

**API 限流**：15 req/s（per appKey），全部专用于同步。每轮增量同步 ~15-21 次 API 调用（~3-4 秒），远低于限额。

| 单据类型 | API调用方式 | 原因 | 每轮API调用 |
|---------|-----------|------|-----------|
| **采购单** | list（翻页）+ 逐个detail | list不含商品编码，detail含 | 2-3次 |
| **售后单** | list（翻页） | list直接含 `items[].mainOuterId/outerId` | 1-2次 |
| **收货单** | list（翻页）+ 逐个detail | list不含商品编码 | 1-2次 |
| **上架单** | list（翻页）+ 逐个detail | list不含商品编码 | 1-2次 |
| **采退单** | list（翻页）+ 逐个detail | list不含商品编码，detail含 outerId/itemOuterId | 1-2次 |
| **订单** | list（翻页，`time_type=upd_time`） | list 返回 `orders[].sysOuterId/outerSkuId`，无需detail | 2-4次 |
| **商品** | `item.list.query`（翻页，`startModified/endModified`） | 增量拉取变更商品+SKU | 1-2次 |
| **库存** | `stock.api.status.query`（翻页，`startStockModified/endStockModified`） | 增量拉取库存变动的SKU | 1-3次 |
| **供应商** | `supplier.list.query`（全量，`fetch_all`） | 数据量极小（几十~几百家），全量覆盖 | 1次 |
| **平台映射** | `erp.item.outerid.list.get`（翻页，`startTime/endTime`） | 商品↔平台对应关系，下架检查用 | 0次（每6小时1-2次） |
| **合计** | | | **~13-19次/轮** |

**同步频率**：
- **高频（~1分钟/轮）**：采购/收货/上架/采退/售后/订单/商品/库存/供应商，共 9 种类型依次执行，总耗时 ~3-4 秒
- **低频（每6小时）**：平台映射，数据变动极少（商品上下架时才变），无需每轮同步

**订单同步特殊说明**：
- 使用 `upd_time`（修改时间）而非 `created`，一次性捕获新增订单 + 状态变更（退款/发货/审核等）
- 无需调用 detail API，`erp.trade.list.query` 返回的 `orders[]` 子订单直接包含商品编码
- 不存储敏感信息（收件人/电话/地址/买家昵称），功能层面不提供此类查询
- 存储备注信息：`sellerMemo`→remark、`sysMemo`→sys_memo、`buyerMessage`→buyer_message
- 每个订单平均含 ~3 个子商品，入库以子商品为粒度（每个子商品一行）

**商品同步特殊说明**：
- 使用 `item.list.query` + `startModified/endModified` 增量拉取变更商品
- 日期精度为天（`YYYY-MM-DD`），需回溯1天避免边界遗漏
- 每个商品拉取后，解析 SKU 列表分别写入 `erp_products` 和 `erp_product_skus`
- 套件类型（type=1,2）同时存储 `suit_singles` 到 `erp_products.suit_singles`（JSONB）
- 额外字段映射：`shipper` ← 货主名称、`remark` ← 商品备注（HTML清洗）、`created_at`/`modified_at` ← 时间戳、`pic_url` ← `picPath`（商品主图URL）
- SKU 级也有 `shipper`（货主名称），写入 `erp_product_skus.shipper`；SKU图片 `pic_url` ← `skuPicPath`

**库存同步特殊说明**：
- 使用 `stock.api.status.query` + `startStockModified/endStockModified` 增量拉取库存变动的 SKU
- 日期精度为天（`YYYY-MM-DD`），需回溯1天避免边界遗漏
- 每条记录包含：总库存/可售/锁定/采购在途/销退在途/调拨在途/退款库存/入库暂存/残次品/虚拟库存/状态/仓库ID/更新时间
- upsert 以 `(outer_id, sku_outer_id)` 为唯一键，覆盖更新
- 库存变动频繁（出库/入库/锁定），每轮增量可能有 50-200 条变更，需 1-3 次翻页
- 新增字段：`allocate_num`←allocateNum, `refund_stock`←refundStock, `purchase_stock`←purchaseStock, `warehouse_id`←wareHouseId, `stock_modified_time`←stockModifiedTime
- extra_json 存放低频字段：brand, cidName, unit, place, itemBarcode, skuBarcode

**供应商同步特殊说明**：
- 使用 `supplier.list.query`（`fetch_all=True, page_size=500`）全量拉取供应商列表
- 数据量极小（通常几十到几百家），每轮全量覆盖，无需增量逻辑
- upsert 以 `code`（供应商编码）为唯一键
- 包含字段：名称/编码/状态/联系人/手机/电话/邮箱/分类/结算方式/交期/地址/备注

**平台映射同步特殊说明**：
- 使用 `erp.item.outerid.list.get` + `startTime/endTime` 增量拉取变更映射
- **每6小时同步一次**（数据变动极低频，仅商品上下架时变化）
- 每条记录包含：`outerId`（ERP编码）、`numIid`（平台商品ID）、`userId`（店铺ID）、`title`（平台商品名）
- SKU级映射存入 `sku_mappings` JSONB：`[{"skuOuterId":"XX01-01","skuNumIid":"123456"},...]`
- upsert 以 `(outer_id, num_iid)` 为唯一键
- 首次全量：翻页拉取全部映射（~10,000条，~200次API调用，~2分钟）

**备注/创建人同步**（已纳入各类型字段映射中）：
- 采购单 detail：`remark`→remark, `createrName`→creator_name, `deliveryDate`→delivery_date
- 收货单 detail：`createrName`→creator_name, `purchaseOrderCode`→purchase_order_code
- 售后 list：`remark`→remark（工单级），详见上方售后同步字段映射
- 订单 list：`sellerMemo`→remark, `sysMemo`→sys_memo, `buyerMessage`→buyer_message

#### 7.2 增量同步逻辑

```
每次同步：
1. 读取 sync_state.last_sync_time
2. 计算时间窗口：start = last_sync_time - 回溯量，end = NOW
   ⚠ 窗口过大保护：若 end - start > 7天（如服务宕机24h+），自动切换为分片模式：
   按7天一片分割时间窗口，逐片执行步骤 3-6，每片完成后更新 sync_state.last_sync_time。
   复用首次全量的分片逻辑，防止单次请求数据量过大导致超时或内存溢出。
3. 请求 API（startModified = start，endModified = end）
4. 翻页拉取全部变更记录
5. 对需要 detail 的类型，逐个拉取详情
6. 解析商品明细行，upsert 到 erp_document_items
7. 聚合计算：对本轮涉及的商品+日期，重算 erp_product_daily_stats（upsert）
8. 更新 sync_state（last_sync_time, total_synced, status）
```

**回溯策略**：单据类型回溯5分钟（时间精度到秒），商品类型回溯1天（日期精度到天）。防止边界遗漏。
**聚合伴随同步**：每次同步完自动触发当日聚合，保证统计数据实时性。

**订单同步补充**：
- 步骤2中，订单使用 `timeType=upd_time` 而非 `startModified`，捕获所有状态变更
- 步骤4不需要（订单 list 已含商品编码，无需 detail）
- 步骤5中，每个订单的 `orders[]` 子订单逐行 upsert，`doc_id=sid`，`order_no=tid`，`platform=source`
- 额外记录订单特有字段映射：
  - `order_status` ← `sysStatus`
  - `express_no` ← `outSid`
  - `express_company` ← `expressCompanyName`
  - `shop_name` ← `shopName`
  - `platform` ← `source`
  - `warehouse_name` ← `warehouseName`（订单也有仓库）
  - `pay_time` ← `payTime`
  - `consign_time` ← `consignTime`
  - `cost` ← `orders[].cost`（子商品成本）
  - `refund_status` ← `orders[].refundStatus`（子商品退款状态）
  - `discount_fee` ← `discountFee`（订单级折扣，按子商品 payment 比例均摊到每行。⚠ 最后一个子商品用 `总折扣-前N-1个之和` 兜底精度）
  - `post_fee` ← `postFee`（运费，**仅 item_index=0 首行存值**，其余行 NULL。防止 SUM 重复）
  - `gross_profit` ← `grossProfit`（毛利，**仅 item_index=0 首行存值**，其余行 NULL。聚合层用 `SUM(amount)-SUM(cost*quantity)` 计算，不依赖此字段）
  - `remark` ← `sellerMemo`（卖家备注，订单级，各子商品行共享）
  - `sys_memo` ← `sysMemo`（系统备注）
  - `buyer_message` ← `buyerMessage`（买家留言）
  - `extra_json` ← type(订单类型), payAmount(实付), isCancel/isRefund/isExcep/isHalt/isUrgent(标记), payment(子商品实付)

**售后同步字段映射**：
- 售后 list 返回完整工单+嵌套商品，无需 detail API
- 工单级字段：
  - `doc_id` ← `id`（工单ID）
  - `doc_status` ← `status`（工单状态）
  - `doc_created_at` ← `created`
  - `shop_name` ← `shopName`
  - `platform` ← `source`（售后也有平台来源）
  - `order_no` ← `tid`（关联原订单的平台订单号）
  - `aftersale_type` ← `afterSaleType`（1退款/2退货/3补发/4换货/5未发退）
  - `refund_money` ← `refundMoney`（系统退款金额）
  - `raw_refund_money` ← `rawRefundMoney`（平台实退金额）
  - `text_reason` ← `textReason`（售后原因）
  - `finished_at` ← `finished`（完结时间）
  - `remark` ← `remark`（工单备注）
  - `extra_json` ← goodStatus, refundWarehouseName, refundExpressCompany/Id, reissueSid, platformId, shortId
- 商品级字段（嵌套 `items[]`，每个商品一行）：
  - `outer_id` ← `mainOuterId`
  - `sku_outer_id` ← `outerId`
  - `item_name` ← `title`
  - `quantity` ← `receivableCount`（申请数）
  - `real_qty` ← `itemRealQty`（实退数）
  - `price` ← `price`
  - `amount` ← `payment`（实付金额）
  - `extra_json` ← goodItemCount(良品), badItemCount(次品), type(处理方式)
  - ⚠ **items[] 为空时**（仅退款 type=1,5 等无实物退货的工单）：仍插入一行，`outer_id=NULL, sku_outer_id=NULL, item_index=0, item_name=NULL`，工单级字段（aftersale_type/refund_money/text_reason/shop_name/order_no 等）正常填充。保证工单不丢失，聚合统计完整

**采购单同步补充字段**：
- `delivery_date` ← `deliveryDate`（交货日期，单据级+行级均可能有）
- `extra_json` ← shortId, totalAmount, actualTotalAmount, financeStatus, arrivedQuantity, receiveQuantity(已收货), totalFee, amendAmount

**采退单同步字段映射**：
- 采退单使用 `purchase.return.list.query` list + `purchase.return.list.get` detail 两步同步
- 注意：创建时间字段名为 `gmCreate`（非 `created`）
- 单据级字段：
  - `doc_id` ← `id`（采退单ID）
  - `doc_code` ← `code`（采退单号）
  - `doc_status` ← `status`（0=作废/1=待出库/3=已出库/4=出库中/5=草稿）
  - `doc_created_at` ← `gmCreate`（注意字段名差异）
  - `supplier_name` ← `supplierName`
  - `warehouse_name` ← `warehouseName`
  - `creator_name` ← `createrName`
  - `purchase_order_code` ← `purchaseOrderId`（关联采购单ID，可追溯原采购单）
  - `actual_return_qty` ← `actualReturnNum`（单据级实退总数）
  - `extra_json` ← shortId, totalAmount, financeStatus, statusName, tagName
- 明细行字段（detail items[]，每个商品一行）：
  - `outer_id` ← `itemOuterId`（主编码）
  - `sku_outer_id` ← `outerId`（SKU编码）
  - `item_name` ← `title`
  - `quantity` ← `returnNum`（退货数量）
  - `actual_return_qty` ← `actualReturnNum`（行级实退数量）
  - `price` ← `price`
  - `amount` ← `amount`

**收货单同步补充字段**：
- `purchase_order_code` ← `purchaseOrderCode`（关联采购单号）
- `extra_json` ← shelvedQuantity, getGoodNum, getBadNum, totalDetailFee, busyTypeDesc

#### 7.3 首次全量同步

- 按天分片：从90天前开始，每次拉取7天数据
- 采购单历史（>3个月）：使用 `purchase_order_history` API
- 采退单历史（>3个月）：使用 `purchase_return_history` API
- 订单全量回填：90天 × ~10,000单 = ~90万单，按天分片 pageSize=200，约4,500次API调用。每次 API 调用含网络 RT（~100ms）+ DB upsert 200×3=600行（~200ms），实际吞吐 ~3 req/s。总耗时 ~4,500÷3 ≈ 25 分钟（控速 ≤10 req/s，实际受 DB 写入瓶颈限制在 ~3 req/s）
- 商品目录全量：`item.list.query` 翻页拉取全部商品（含 SKU），预估 ~5,000 商品 × ~3 SKU/商品，约 100 次 API 调用，~10分钟
- 库存全量：`stock.api.status.query` 翻页拉取全部 SKU 库存，预估 ~15,000 SKU，pageSize=50 约 300 次 API 调用，~5分钟
- 供应商全量：`supplier.list.query` 一次拉取（fetch_all），几十~几百条，1次 API 调用，几秒完成
- 平台映射全量：`erp.item.outerid.list.get` 翻页拉取全部映射，~10,000条，~200次API调用，~2分钟
- 进度记录：`sync_state.last_sync_time` 记录已完成的时间点，中断后可续传
- **全量/增量切换**：每个 sync_type 独立的 `is_initial_done` 标记。全量完成后设为 true，之后才进入增量模式。中断重启时检查此标记：false → 继续从 last_sync_time 处续传全量；true → 正常增量
- **控速**：首次全量串行执行，控制在 ~10 req/s 以内，避免触发限流

#### 7.4 API 调用量估算

每轮同步（~1分钟）：~15-21 次 API 调用
每日同步：~1440 轮 × ~18 次/轮 ≈ **~26,000 次/天**

| 类型 | 每轮增量 | API调用/轮 | 日调用量 |
|------|---------|-----------|---------|
| 采购单 | ~0-1条 | 2（list+detail） | ~2,880 |
| 售后单 | ~2-3条 | 1-2（list翻页） | ~2,160 |
| 收货单 | ~0-1条 | 1-2（list+detail） | ~2,160 |
| 上架单 | ~0-1条 | 1-2（list+detail） | ~2,160 |
| 采退单 | ~0-1条 | 1-2（list+detail） | ~2,160 |
| 订单 | ~10-15条 | 2-4（list翻页） | ~4,320 |
| 商品 | ~0-2条 | 1-2（list翻页） | ~2,160 |
| 库存 | ~50-200条 | 1-3（list翻页） | ~2,880 |
| 供应商 | 全量覆盖 | 1（fetch_all） | ~1,440 |
| 平台映射 | ~0条（低频） | 0（每6小时1-2次） | ~8 |
| **合计** | | **~15-21** | **~22,328** |

**QPS 验证**：每轮 ~21 次调用在 ~3-4 秒内完成，峰值 ~10 req/s，低于 15 req/s 限额。日均 ~22,000 次调用（+平台映射 ~8次/天），无日调用量限制。

#### 7.5 同步健康监控与可观测性

同步服务作为无人值守的后台任务，必须具备故障自感知能力，否则数据静默停更而查询端无法察觉。

**1. 结构化日志**：
- 每轮同步完成时记录：`sync_type | records_synced | api_calls | duration_ms | status`
- 错误日志包含完整上下文：`sync_type | error_type | error_msg | retry_count | last_success_at`
- 首次全量同步额外记录进度：`sync_type | phase=initial | shard=7/13 | total_records | elapsed_min`

**2. sync_state 健康指标**：
在 `erp_sync_state` 表中，利用已有字段实现被动健康检测：
- `last_run_at`：若 `NOW() - last_run_at > 5分钟`（正常间隔的5倍），说明同步可能卡住
- `error_count`（新增字段，SMALLINT DEFAULT 0）：连续失败计数。每次成功归零，每次失败+1
- `last_error`（新增字段，TEXT）：最近一次错误信息，排查用
- 查询工具层面：当 `error_count >= 3` 或 `last_run_at` 超时，在查询结果末尾附加提示 `⚠ 数据可能未及时更新（同步异常，最后成功：{last_run_at}）`

**3. 首次全量同步进度可见**：
- `sync_state.last_sync_time` 已有分片续传功能，配合日志可知当前进度
- 用户端感知：首次全量期间查询返回部分数据时，附加说明 `ℹ 首次数据同步进行中（已同步至 {last_sync_time}），部分历史数据尚未就绪`

**4. 无需外部监控组件**：
不引入 Prometheus/Grafana 等外部依赖。通过 loguru 结构化日志 + sync_state 表状态 + 查询层主动提示，实现轻量级自监控。后续如需告警（如企微机器人推送），读 sync_state 表即可。

---

### 8. 开发任务拆分

#### 阶段1：数据库 + 同步基础设施

- [ ] 任务1.0：启用 `pg_trgm` 扩展（`CREATE EXTENSION IF NOT EXISTS pg_trgm;`）
- [ ] 任务1.1：创建 `erp_document_items` 表 + 14索引（46字段：含售后类型/退款金额/原因/完结时间/运费/毛利/交货日期/采购单号/采退实退数等）。⚠ 注意 item_index 稳定性排序要求（见唯一约束说明）
- [ ] 任务1.2：创建 `erp_document_items_archive` 表（同结构+同唯一约束，归档 upsert 幂等）
- [ ] 任务1.3：创建 `erp_product_daily_stats` 聚合表 + 索引
- [ ] 任务1.4：创建 `erp_products` + `erp_product_skus` 商品主数据表 + 索引（含 shipper/remark/selling_price/weight/pic_url 等完整字段，标题和规格名用 pg_trgm GIN 索引）
- [ ] 任务1.5：创建 `erp_stock_status` 库存快照表 + 6索引（26字段：含调拨在途/退款库存/入库暂存/仓库ID/更新时间）
- [ ] 任务1.6：创建 `erp_suppliers` 供应商主数据表 + 索引
- [ ] 任务1.7：创建 `erp_product_platform_map` 平台映射表 + 索引
- [ ] 任务1.8：创建 `erp_sync_state` 表（含 `is_initial_done` 字段 + `error_count`/`last_error` 健康监控字段）
- [ ] 任务1.9：实现 `erp_sync_worker.py`（独立 async task + Redis 分布式锁 + DB 锁降级 + ~1分钟轮询 + 低频调度）
- [ ] 任务1.10：实现 `erp_sync_service.py` 核心框架（基类、状态管理、增量时间窗口、窗口过大自动分片、聚合计算用 `COUNT(DISTINCT doc_id)` 去重、item_index 排序稳定性、error_count/last_error 健康状态更新）
- [ ] 任务1.11：`config.py` 添加 `erp_sync_enabled`、`erp_sync_interval`、`erp_archive_retention_days`、`erp_platform_map_interval` 等配置项

#### 阶段2：十种数据同步器

- [ ] 任务2.1：采购单同步器（list+detail，含 remark/createrName/deliveryDate/financeStatus/receiveQuantity，extra_json 存 shortId/totalAmount/amendAmount）
- [ ] 任务2.2：售后单同步器（list 解析，含 afterSaleType/refundMoney/rawRefundMoney/textReason/finished/tid→order_no/source→platform，items[]含 realQty）
- [ ] 任务2.3：收货单同步器（list+detail，含 purchaseOrderCode/createrName，extra_json 存 getGoodNum/getBadNum/busyTypeDesc）
- [ ] 任务2.4：上架单同步器（list + detail 两步）
- [ ] 任务2.5：采退单同步器（list+detail，含 supplierName/warehouseName/gmCreate(注意字段名)/actualReturnNum/purchaseOrderId，extra_json 存 shortId/totalAmount/financeStatus/statusName/tagName）
- [ ] 任务2.6：订单同步器（list 翻页，含 postFee/grossProfit/warehouseName，extra_json 存 type/isCancel/payAmount/payment）
- [ ] 任务2.7：商品同步器（增量拉取，含 shipper/remark/selling_price/weight/pic_url(←picPath/skuPicPath) 等完整字段，HTML清洗备注）
- [ ] 任务2.8：库存同步器（增量拉取，含 allocateNum/refundStock/purchaseStock/wareHouseId/stockModifiedTime）
- [ ] 任务2.9：供应商同步器（`supplier.list.query` 全量覆盖，写入 `erp_suppliers`）
- [ ] 任务2.10：平台映射同步器（`erp.item.outerid.list.get` 增量拉取，每6小时执行，写入 `erp_product_platform_map`）

#### 阶段3：调度 + 归档集成

- [ ] 任务3.1：`main.py` lifespan 启动 `ErpSyncWorker` 独立 async task（`asyncio.create_task` 不阻塞） + shutdown hook
- [ ] 任务3.2：首次全量同步逻辑（分片拉取 + 断点续传 + `is_initial_done` 标记切换，含商品+库存+供应商+平台映射全量+采退历史全量）
- [ ] 任务3.3：`background_task_worker.py` 添加归档任务（每日凌晨，热表→冷表迁移，INSERT+DELETE 同事务，archive 表 upsert 幂等）
- [ ] 任务3.5：商品删除检测（每日凌晨归档后，全量拉商品目录对比，不在列表中标记 `active_status=-1`）
- [ ] 任务3.4：聚合任务（伴随同步自动执行 + 每日全量重算兜底）

#### 阶段4：本地查询工具

- [ ] 任务4.1：实现 `erp_local_query.py`（纯本地查询：5个单据明细(含采退)+流转(含采退)+库存+平台映射工具；days>90时自动UNION冷表）
- [ ] 任务4.2：实现 `erp_stats_query.py`（统计报表查询：月度汇总/销量排行/售后率/趋势对比）
- [ ] 任务4.3：实现 `local_product_identify`（纯本地编码识别+商品名搜索+规格名搜索，查 erp_products/skus/suppliers + 关联单据统计 + 图片URL展示）
- [ ] 任务4.4：`erp_tools.py` 注册八个本地查询工具定义
- [ ] 任务4.5：`erp_prompt.py` 更新 Phase2 提示词（告知 Brain 新工具能力）
- [ ] 任务4.6：`code_identifier.py` 改造（`erp_identify` 工具改为本地 DB 优先，API 仅作补充）

#### 阶段5：测试 + 验证

- [ ] 任务5.1：同步服务单元测试（含聚合计算+商品同步+库存同步+供应商同步+平台映射同步+采退同步）
- [ ] 任务5.2：本地查询+统计查询+库存查询+编码识别+下架检查工具单元测试
- [ ] 任务5.3：归档任务单元测试
- [ ] 任务5.4：端到端验证（同步数据 → AI 查询 → 返回结果）

---

### 9. 依赖变更

- **Python 无新增依赖**（全部使用现有 supabase/httpx/redis/loguru/tenacity）
- **PostgreSQL 需启用 `pg_trgm` 扩展**（Supabase 已内置，执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm;` 即可）——用于商品名/规格名的中文子串模糊搜索 GIN 索引

---

### 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 采购单 detail 逐个请求慢 | 中 | 串行请求+重试；增量模式下单次detail数量少（~1-2个） |
| 售后量大首次全量同步耗时 | 中 | 按天分片+断点续传，后台静默执行 |
| API 日期格式不统一 | 低 | 同步器内按单据类型硬编码格式，有注释说明 |
| Supabase 写入性能 | 低 | upsert 批量提交（每100条一批），索引在写入后建 |
| 同步数据与实际有 ≤1 分钟延迟 | 低 | ~1分钟/轮同步，ERP 运营场景可接受，无需额外处理 |
| 三个月前采购/采退单查不到 | 低 | 首次全量用 `purchase_order_history`/`purchase_return_history`；增量模式只管近期 |
| 归档迁移中断 | 低 | 分批执行+事务保证；中断后下轮继续，不丢数据 |
| 聚合数据与明细不一致 | 低 | 每日全量重算兜底；upsert 幂等 |
| 冷表数据量长期膨胀 | 低 | 聚合表永久保留统计，冷表可配置超12个月清理 |
| 多 Worker 重复同步 | 高 | Redis 分布式锁 `SET NX EX`，只有1个 worker 执行 |
| 同步阻塞图片/视频轮询 | 中 | 独立 async task，不在 BackgroundTaskWorker 循环内 |
| 订单量大热表膨胀（日均3万行） | 中 | 3个月归档保持~123万行稳定；PostgreSQL 百万级+索引仍毫秒级 |
| 订单首次全量回填耗时（~25分钟） | 中 | 按天分片+断点续传，后台静默执行，不影响其他同步 |
| 订单状态频繁变更导致重复写入 | 低 | upsert 幂等覆盖，同一订单多次变更只保留最新状态 |
| 敏感信息不提供查询 | 低 | 设计决策：不存储收件人/电话/地址/买家昵称；买家留言(buyerMessage)仅用于运营参考 |
| 商品备注含 HTML | 低 | 同步时清洗 `<br/>`/`<br>` → 空格，截断超长内容 |
| 商品同步日期精度仅到天 | 低 | `item.list.query` 的 `startModified` 为天级，回溯1天+upsert 确保不遗漏 |
| 货主字段 `shipper` 为非必填 | 低 | API文档标注非必填，部分商品可能无货主。`shipper` 字段允许 NULL，查询时兼容 |
| API 15 req/s 限流 | 低 | 每轮同步峰值 ~10 req/s，预留 33% 余量；首次全量同步串行控速 |

---

### 11. 文档更新清单

- [ ] FUNCTION_INDEX.md（新增同步服务+本地查询函数）
- [ ] TECH_ARCHITECTURE.md（新增ERP本地索引子系统说明）

---

### 12. 对外影响分析与死代码排查

#### 对现有组件的影响

| 影响点 | 风险 | 文件 | 说明 |
|-------|------|------|------|
| **code_identifier.py 改造** | 高 | `code_identifier.py` | 改为"本地 DB 优先+API 补充"。⚠ 必须保留完整 API fallback 链路（`_identify_product` / `_identify_order` / `_identify_barcode`）。本地 DB 未同步完成时（启动前10分钟），identify 调用会走 API 降级，效果与改造前一致。实现时加集成测试覆盖"DB 无数据"场景 |
| **main.py lifespan 启动时间** | 中 | `main.py` | 新增 `ErpSyncWorker` 必须用 `asyncio.create_task()` 立即返回，**不能阻塞等待首次全量同步完成**。否则 uvicorn 启动超时（默认 30s）。首次全量同步在后台静默执行 |
| **Supabase 连接数竞争** | 中 | `database.py`, `knowledge_config.py` | 现有：4 worker × (1 Supabase REST + 3 psycopg KB) = 16 连接。新增同步：每 worker 启动但仅 1 个获得锁执行，需 1 条 psycopg 连接串行操作。**实现要求**：同步服务复用 `knowledge_config.py` 的 psycopg pool 或新建独立 pool（max_size=1），严禁并发开多连接 |
| **erp_tools.py 工具数量膨胀** | 低 | `erp_tools.py`, `erp_prompt.py` | 现有 9 工具+新增 8 = 17 工具。路由提示词 token 翻倍。**建议**：本地查询工具上线后，将对应 API 工具（如 `stock_status`、`purchase_order_list`）从路由提示词中降级/隐藏，优先引导 Brain 走本地工具。API 工具作为 fallback 保留但不主动暴露 |

#### 死代码排查

| 文件 | 状态 | 说明 |
|------|------|------|
| `service.py`（`KuaiMaiService` 类） | **已废弃** | 560+ 行，定义 `query_orders()`/`query_products()` 等方法 + `_STOCK_STATUS_MAP`/`_STOCK_STATUS_LABELS` 常量。已被 `ErpDispatcher` + Registry 模式完全替代，当前无任何文件 import。**建议在本项目阶段1完成后清理删除**，避免新开发者误引用 |
| `trade.py` 的 `use_has_next`/`use_cursor` 参数 | 定义未使用 | 合法 API 参数但 AI 查询场景从未触发（翻页由 `_fetch_all_pages` 内部处理）。同步器也不走工具定义。**无需清理**，但可从路由提示词中移除以减少 token |
| `local_product_identify` 上线后 `code_identifier._identify_product()` 的 API 分支 | **不删除** | 本地 DB 建好后 >95% 调用走本地，但 API 分支必须保留：(1) 首次同步未完成时 fallback (2) DB 数据延迟时补充 (3) 订单识别仍需 API（仅存 90 天） |

---

### 13. 设计自检

- [x] 连锁修改已全部纳入任务拆分（worker/config/tools/prompt/identifier 共5处）
- [x] 边界场景均有处理策略（见第2节，18个场景）
- [x] 所有新增文件预估 ≤ 500行（`erp_sync_worker.py` ~120行，`erp_sync_service.py` ~500行含6种同步器，`erp_local_query.py` ~300行，`erp_stats_query.py` ~200行）
- [x] 多 Worker 部署安全：Redis 分布式锁防重复同步
- [x] 不阻塞现有任务：独立 async task，与 BackgroundTaskWorker 并行
- [x] 无模糊版本号依赖（无新增依赖）
- [x] 纯本地查询架构：API 全部用于同步，查询零 API 消耗，100人并发无压力（见第6.0节）
- [x] 三层数据架构 + 商品主数据层 + 库存层 + 供应商层 + 平台映射层：热表 + 聚合表 + 冷表 + 商品目录 + 库存快照 + 供应商目录 + 平台映射
- [x] 热表数据量可控（~123万行稳定），归档策略配置化
- [x] 十种数据全覆盖：采购/收货/上架/采退/售后/订单/商品/库存/供应商/平台映射，查询+识别+库存+下架检查完整闭环
- [x] 备注信息全覆盖：采购remark/售后remark+textReason/订单sellerMemo+sysMemo+buyerMessage/商品remark
- [x] 货主：商品表+SKU表含 `shipper`（货主名称，API字段已确认）
- [x] 售后字段完整：afterSaleType/refundMoney/rawRefundMoney/textReason/finished/tid→order_no/realQty
- [x] 订单字段完整：postFee(运费)/grossProfit(毛利)/warehouseName(仓库)，extra_json含isCancel/type/payAmount
- [x] 采购字段完整：deliveryDate(交货日期)，extra_json含financeStatus/totalAmount/receiveQuantity
- [x] 采退字段完整：actualReturnNum(实退数)/purchaseOrderId(关联采购单)/gmCreate(创建时间)，extra_json含financeStatus/statusName/tagName
- [x] 收货字段完整：purchaseOrderCode(关联采购单)，extra_json含getGoodNum/getBadNum
- [x] 库存字段完整：allocateNum/refundStock/purchaseStock/wareHouseId/stockModifiedTime
- [x] 同步频率 ~1分钟/轮，数据延迟 ≤1分钟，等同实时
- [x] API 限流安全：每轮峰值 ~10 req/s，低于 15 req/s 限额，预留 33% 余量（含采退单同步）
- [x] 不存储敏感信息：设计决策层面不提供收件人/电话/地址/买家昵称查询功能（买家留言保留用于运营参考）
- [x] 数据充分性验证：覆盖销售/毛利/售后/采购/物流时效/退款/供应商7大分析维度（见第13节）
- [x] 商品编码识别本地化：`local_product_identify` 替代 API 调用，毫秒级识别，支持商品名搜索+规格名搜索+图片URL展示
- [x] 下架检查：`local_platform_map_query` 查询ERP编码↔平台商品映射，每6小时低频同步
- [x] 商品名称搜索：`erp_products.title` pg_trgm GIN索引 + ILIKE，支持中文子串模糊搜索
- [x] 规格名称搜索：`erp_product_skus.properties_name` pg_trgm GIN索引 + ILIKE，支持中文子串搜索
- [x] 图片URL存储：`erp_products.pic_url`←picPath、`erp_product_skus.pic_url`←skuPicPath，供展示+未来拍照识别
- [x] API字段全对标：各表字段已与API返回字段逐一比对，关键字段独立存储，低频字段存extra_json
- [x] **审查修复**：daily_stats 唯一键 COALESCE 处理 NULL（BUG-1）
- [x] **审查修复**：item_index 稳定性——入库前按确定性字段排序再分配（BUG-2）
- [x] **审查修复**：售后类型枚举完整覆盖 0~9 全部类型 + 聚合表 reject/repair/other 计数（BUG-3）
- [x] **审查修复**：归档 INSERT+DELETE 同事务 + archive 表加唯一约束幂等兜底（BUG-4）
- [x] **审查修复**：增量窗口过大自动切分片模式（BUG-5）
- [x] **审查修复**：discount_fee 尾差兜底策略（BUG-7）
- [x] **审查修复**：gross_profit/post_fee 仅首行存值，聚合层不依赖 SUM（BUG-8）
- [x] **审查修复**：Redis 锁降级到 DB 锁，upsert 幂等兜底（EDGE-1）
- [x] **审查修复**：首次全量耗时估算修正为 ~25 分钟（EDGE-2）
- [x] **审查修复**：售后 items 为空时仍插入一行保留工单信息（EDGE-3）
- [x] **审查修复**：中文搜索改用 pg_trgm GIN 索引 + ILIKE 替代 simple 分词器（EDGE-5）
- [x] **审查修复**：sync_state 新增 is_initial_done 标记，防首次全量中断后误入增量（EDGE-6）
- [x] **审查修复**：商品删除检测——每日全量对比标记 active_status=-1（EDGE-4）
- [x] **审查修复**：对外影响分析 4 项 + 死代码排查 3 项（见第12节）
- [x] **审查修复R2**：aftersale outer_id=NULL 工单无法聚合到 daily_stats，查询层补偿（NEW-1）
- [x] **审查修复R2**：stat_date 语义明确为 `doc_created_at::date`，保证同日分子分母一致（NEW-2）
- [x] **审查修复R2**：DB 锁降级 TOCTOU 竞态修复，改为原子 CAS UPDATE...RETURNING（NEW-3）
- [x] **审查修复R2**：热表复合索引加入 `doc_created_at DESC`，优化日期范围查询（NEW-4）
- [x] **审查修复R2**：发货时效 SQL 过滤改为 `pay_time` 而非 `doc_created_at`（NEW-5）
- [x] **审查修复R2**：新增同步健康监控设计——error_count/last_error 字段 + 查询层异常提示（NEW-6）
- [x] **审查修复R3**：聚合 `*_count` 必须用 `COUNT(DISTINCT doc_id)` 去重——子商品粒度存储下 COUNT(*) 会严重超算（G1）
- [x] **审查修复R3**：聚合条件明确——shipped/finished/refund/cancelled 各字段的 FILTER 条件（G7）
- [x] **审查修复R3**：明细查询 days>90 自动 UNION 冷表，对用户透明（G6）
- [x] **审查修复R3**：售后查询工具 type 参数描述补全 0~9 全部类型（G5）

---

### 13. 数据充分性评估

对标电商运营主流分析场景，逐一验证数据覆盖情况：

| 分析场景 | 覆盖度 | 数据来源 | 说明 |
|---------|--------|---------|------|
| **销售分析**（销量/金额/趋势） | 95% | 聚合表 order_count/qty/amount | 按商品/日期/周期任意聚合 |
| **毛利分析**（成本/利润/利润率） | 95% | 聚合表 order_amount - order_cost | 聚合层用 `SUM(amount)-SUM(cost*qty)` 计算；gross_profit 仅 item_index=0 首行存参考值。含运费 post_fee（同样仅首行存）可算净利 |
| **售后分析**（售后率/类型分布/金额/原因） | 95% | 热表 aftersale_type/refundMoney/textReason + 聚合表 | 类型/金额/原因/处理时效全覆盖 |
| **退款分析**（退款率/取消率/平台实退） | 90% | 热表 refund_money/raw_refund_money + 聚合表 | 系统退款vs平台实退对比 |
| **采购分析**（到货率/采购金额/交期/采退） | 98% | 聚合表 + 热表 delivery_date + purchase_return | 含交货日期+采退单，采购全链路闭环 |
| **物流时效**（发货速度/24h率） | 85% | 热表 pay_time + consign_time | 支付→发货时间差，按快递公司分析 |
| **平台对比**（各平台销售/售后占比） | 90% | 热表 platform（订单+售后均有） | 售后也有平台维度，对比更全面 |
| **店铺对比**（各店铺业绩） | 85% | 热表 shop_name + 索引 | 需扫热表聚合，非秒级但可接受 |
| **库存分析**（库存状态/预警/在途/多仓） | 95% | erp_stock_status | 总库存/可售/锁定/调拨/退款/在途+仓库ID多仓区分 |
| **供应商分析**（供货关系/交期/分类） | 90% | erp_suppliers + erp_document_items | 供应商主数据+采购单关联供应商 |
| **商品名/规格名搜索** | 95% | erp_products.title pg_trgm + erp_product_skus.properties_name pg_trgm | 商品名 ILIKE 中文子串搜索+规格属性搜索，毫秒级 |
| **商品图片展示** | 90% | erp_products.pic_url + erp_product_skus.pic_url | 商品主图+SKU图片URL，识别结果附带图片 |
| **货主归属**（商品→货主关联） | 95% | erp_products.shipper + erp_product_skus.shipper | 货主名称直接存储，可按货主汇总商品/销量/库存 |
| **备注/留言分析**（运营标注/客户需求） | 85% | erp_document_items + erp_products | 卖家备注/系统备注/买家留言/商品备注 |
| **下架检查**（平台商品↔ERP映射） | 95% | erp_product_platform_map | ERP编码→平台商品ID→店铺，下架前影响评估 |
| **全链路追踪**（采购→销售→售后→采退） | 95% | local_product_flow 工具 | 6阶段完整闭环（含采退） |
| **客户复购/地域分析** | 0% | ❌ 平台限制 | 淘系/拼多多不返回买家信息，非设计问题 |

**拍照识别（后续增值功能，不阻塞本期）**：
- 方案：多模态AI（Gemini/Claude）识别照片中的文字（编码/条码/品名）→ 查本地DB → 返回匹配商品+存储图片供确认
- 前提：`pic_url` 已存储（本期完成），多模态模型基础设施已有
- 开发量：~1-2天（prompt工程+结果匹配逻辑）
- 适合场景：包装上有编码/条码/商品名的商品（覆盖绝大多数ERP商品）

**未覆盖的长尾场景（平台API限制，非设计缺陷）**：
- 客户复购率：需要买家ID，淘系/拼多多匿名化处理
- 地域分析：需要收货地址，淘系/拼多多不返回
- 竞品分析：ERP无竞品数据
- 广告ROI：需要广告平台数据，ERP不含

---

**确认后保存文档并进入开发（`@4-implementation`）**

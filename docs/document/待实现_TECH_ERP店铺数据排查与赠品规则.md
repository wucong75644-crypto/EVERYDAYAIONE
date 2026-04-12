# ERP 店铺数据排查与赠品规则讨论

> 日期：2026-04-10 ~ 2026-04-11
> 背景：Agent 做"4月8日 vs 4月9日 全店铺付款订单对比"时，遗漏拼多多 + 把店铺名和平台名混淆

---

## 一、本次排查发现的所有问题

### 1.1 平台编码两套体系（已修复）

**核心问题**：两张表用了完全不同的 platform 编码，互不兼容。

| 平台 | `erp_shops` 表（同步前）| `erp_document_items` 表 |
|------|-------|-------|
| 拼多多 | `拼多多`（中文）| `pdd`（英文）|
| 京东 | `京东`（中文）| `jd`（英文）|
| 快手 | `快手`（中文）| `kuaishou`（英文）|
| 小红书 | `小红书`（中文）| `xhs`（英文）|
| 抖音放心购 | `fxg` | `fxg` |
| 淘宝 | `tb` | `tb` |
| 1688 | `1688` | `1688` |

**根因**：`erp_sync_config_handlers.py` 的 `_PLATFORM_MAP` 把快麦 API 返回的英文 source 翻译成了中文。

**后果**：
- `local_shop_list` 查 `erp_shops`，分组标题显示 `【拼多多】`
- `local_global_stats` 查 `erp_document_items`，过滤参数要传 `platform='pdd'`
- Agent 拿到 `拼多多` 后传给 stats → **零结果**

### 1.2 工具 enum 错误（已修复）

`local_global_stats` / `local_order_query` / `local_compare_stats` 的 platform enum 写的是 `["tb","jd","pdd","dy","xhs","1688"]`：
- `dy` 在数据库里**根本不存在**（抖音存的是 `fxg`，82k+ 条数据）
- `kuaishou`（15k 条）**完全不在 enum 里**，Agent 不知道能查

**结果**：用户问"查抖音订单"，Agent 传 `platform="dy"` → 一直零结果，从来没生效过。

### 1.3 RPC group_by=shop 合并跨平台同名店铺（已修复）

数据库里有大量同名店铺跨平台存在：
- "蓝恩集美优品" 在 pdd + fxg + sys
- "三天饿九顿呀" 在 pdd + fxg + sys
- "蜜桃格格巫" 在 pdd + tb + sys
- "快乐的小癫子" 在 tb + 快手 + sys

旧 RPC `GROUP BY shop_name` 把不同平台的同名店铺合并成一行，且不返回 `platform` 字段，Agent 无法区分。

### 1.4 修复方案（已上线）

| 修复项 | 改动 | 文件 |
|--------|------|------|
| 1. platform 编码统一 | `_PLATFORM_MAP` 中文→英文 + DB 存量 131 行迁移 | `erp_sync_config_handlers.py` + 056 迁移 |
| 2. RPC shop 分组加 platform | `group_by=shop` 改为 `GROUP BY shop_name, platform`，返回 platform 字段 | `056_unify_platform_codes.sql` |
| 3. 格式化标注平台 | shop 分组输出 `店铺名[淘宝]` 格式 + shop_list 中文显示 | `erp_local_global_stats.py` + `erp_local_query.py` |
| 4. 工具 enum 修正 | `dy`→`fxg`，加 `kuaishou`，所有 enum 带中文说明 | `erp_local_tools.py` 3 处 |

---

## 二、附带发现的问题（未修复）

### 2.1 拼多多 110 万条订单全部金额=0

**根因**：快麦 API 限制，pdd 不返回订单号(tid) 和金额(payment)，只返回系统单号。

**影响**：按金额统计时，pdd 店铺看起来都是 0。

**解决方向**：对接方舟（拼多多官方）后才能拿到金额，**不是代码 bug**。

### 2.2 erp_shops 表中的非主流平台

`erp_shops` 里还有这些 platform 值：
- `dangkou`（档口）2 个
- `shopee`（虾皮）2 个
- `wxsph`（微信视频号）1 个
- `wd`（微店）1 个
- `alibabac2m`（阿里 C2M）1 个

数据量很小，未来如果有需求再处理。

### 2.3 sys 平台订单（28k+ 条）

**性质**：快麦系统内部对账单据（默认店铺/线下订单导入），各平台都有，金额全部为 0。

**影响**：混在统计里会拉低店铺均值。但因为 `COUNT(DISTINCT doc_id)` 不会多算订单数，金额统计也是 0 不影响 SUM，**实际影响很小**。

---

## 三、赠品标记问题（讨论后决定不修）

### 3.1 问题描述

`erp_document_items` 一个订单可能存多行，每个商品/赠品一行：

```
订单 5796242553058205:
  item_0: BZTZZP01 "随机小礼品"      price=0.00  amount=0.00  ← 赠品
  item_1: TJ-YQJBLKC01 "亚克力卡册"  price=26.99 amount=24.99 ← 主商品
  item_2: ZPHPK01 "随机幸运现金券"   price=0.00  amount=0.00  ← 赠品
```

### 3.2 影响分析（数据已确认）

**赠品占比**：
- 赠品行占总 item 行的 **25%**（51,208 / 201,212，排除 pdd/sys）
- 赠品数量占总数量的 **22%**（70,661 件 / 308,471 件）
- 74% 的订单是单 item 订单（无赠品），26% 是多 item 订单

**对统计的实际影响**：

| 查询场景 | 是否受影响 | 严重程度 |
|---------|-----------|---------|
| 按店铺统计订单数（`COUNT DISTINCT doc_id`）| ✅ 正确 | - |
| 按店铺统计金额（`SUM(amount)`）| ✅ 正确（赠品 0 元不影响）| - |
| **按店铺统计数量（`SUM(quantity)`）** | ❌ 多算 22% | 中 |
| **按商品统计销量** | ❌ 赠品 SKU 被算成"销量第一" | **严重** |
| **商品销量 TOP10 排名** | ❌ 前几名很可能全是赠品 SKU | **严重** |
| 列出某订单详情 | ⚠️ 用户看不出哪个是赠品 | 体验问题 |

**TOP10 实际验证**（4/10 是赠品）：

```
含赠品 TOP10:
  PDPJ01     拼豆急救包                qty=48797 avg=21.92
  BLTMH02    随机赠品链接              qty=20513 avg=0.12  ← 赠品
  ZPHPK01    评价优惠券                qty=16415 avg=0.01  ← 赠品
  ZCRJTZS01  黑巧A7活页本              qty=8801  avg=34.63
  None       同学录                    qty=7002  avg=34.95
  BLTMH01    便签纸                    qty=6940  avg=0.23  ← 赠品
  200BCB01   分栏草稿本                qty=5902  avg=32.08
  TJ-GZMTZS01 拼贴贴纸                  qty=5751  avg=16.51
  BZTZZP01   小猫御守                  qty=5659  avg=2.19  ← 赠品
  MKLBD      马卡龙笔袋                qty=4605  avg=25.11
```

### 3.3 赠品的精确定义（如果以后要修）

**简单规则 `amount = 0` 不够精确**，会误标 5 类 0 元数据：
1. ✅ 真赠品：fxg/tb/jd/kuaishou/xhs 同订单内主商品有金额、配套行 0 元
2. ❌ pdd 全部：API 限制，不是赠品
3. ❌ sys 系统单据：业务对账单，不是赠品
4. ❌ 1688 整单 0 元（277 单）：批发样品
5. ❌ jd 整单 0 元（1088 单）：京喜规则单

**精确定义**：
> 同一订单内（同 `doc_id` + 同 `org_id`），存在 `amount > 0` 的行，且当前行 `amount = 0`，则当前行为赠品。
> `platform IN ('pdd', 'sys')` 的行不参与判断。

### 3.4 为什么暂时不修

**实际使用场景的影响很小：**
- 公司内部使用，看到 `ZPHPK01 现金券` 一眼就知道是赠品
- 没有外部客户，不会因为数据被投诉
- 做业务决策时通常会让 Agent 列出明细，不会盲信 TOP10

**而修复的代价：**
- 改 6+ 个文件
- 200 万行历史数据回填
- 同步路径多一步逻辑，增量同步边界容易出 bug
- 引入 `is_gift` 字段后，所有未来查询都要考虑"要不要带这个过滤"
- 规则错了回滚麻烦

**业务规则不稳定：**
- pdd 接方舟之后，赠品判断口径可能要变
- 现在写的代码到时候可能要重写
- 这种"业务规则不稳定"的字段，过早固化反而是负债

### 3.5 触发重新讨论的条件

任一情况出现时重新评估：

1. **接入第一个外部客户** — 客户可能不能容忍统计偏差
2. **因为统计被赠品干扰做出错误业务判断** — 实际产生损失
3. **pdd 对接方舟拿到金额后** — 赠品规则需要重定义时一起做
4. **新增需求需要"区分赠品和主商品"** — 比如要做赠品成本核算

### 3.6 实施方案备忘（如果以后要修）

**DB 层**：
```sql
ALTER TABLE erp_document_items ADD COLUMN is_gift BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX idx_doc_items_gift ON erp_document_items (doc_type, is_gift) WHERE is_gift = true;

-- 历史回填
UPDATE erp_document_items d
SET is_gift = true
WHERE doc_type = 'order'
  AND platform NOT IN ('pdd', 'sys')
  AND (amount = 0 OR amount IS NULL)
  AND EXISTS (
    SELECT 1 FROM erp_document_items d2
    WHERE d2.doc_id = d.doc_id
      AND d2.doc_type = d.doc_type
      AND d2.org_id IS NOT DISTINCT FROM d.org_id
      AND d2.amount > 0
  );
```

**同步层**：在 `_batch_upsert` 写入订单后，对涉及的 `doc_id` 集合跑一次小范围 UPDATE 重算 `is_gift`（解法 Y，避免触发器开销）。

**RPC 层**：`erp_global_stats_query` 默认 `WHERE NOT is_gift`，加 `p_include_gifts BOOLEAN DEFAULT false` 参数。

**展示层**：明细查询的 item 列表，`is_gift=true` 的行加 `[赠品]` 前缀。

**`query_doc_items` 默认行为**：不带过滤，由调用方按场景决定。统计/排名场景显式排除，明细场景显式包含。

---

## 四、附带修复的其他 bug（同一会话内）

### 4.1 `_fmt_dt` 未定义 → goods_section 同步失败

**根因**：`8b609a9` 修复 `_fmt_d → _fmt_dt` 时只改了调用处，漏加 import。

**修复**：`erp_sync_piggyback_handlers.py` 加入 `_fmt_dt` 导入。

### 4.2 `mv_kit_stock` 物化视图并发刷新永远失败

**根因**：`053` 迁移用了 `COALESCE(org_id, ...)` 表达式索引，PG `REFRESH MATERIALIZED VIEW CONCURRENTLY` 不允许表达式索引。

**修复**：`055_fix_kit_stock_unique_index.sql` 重建索引为 `(org_id, outer_id, sku_outer_id)`。

### 4.3 `erp_order_logs` upsert 重复行冲突

**根因**：快麦 API 返回的订单操作日志中，同一订单可能有多条 `(system_id, operate_time, action)` 完全相同的记录。

**修复**：`piggyback_order_log` 在 upsert 前按冲突键去重。

### 4.4 阿里云百炼 DashScope 欠费

企微"生成回复时遇到问题"的根因 — 充值后立即恢复，**不是代码 bug**。

---

## 五、相关文件索引

**修改的代码**：
- `backend/services/kuaimai/erp_sync_config_handlers.py` — `_PLATFORM_MAP` 改英文
- `backend/services/kuaimai/erp_local_global_stats.py` — `_format_grouped` 标注平台
- `backend/services/kuaimai/erp_local_query.py` — `local_shop_list` 中文显示
- `backend/services/kuaimai/erp_sync_piggyback_handlers.py` — `_fmt_dt` 导入 + 去重
- `backend/config/erp_local_tools.py` — platform enum 修正

**新增的迁移**：
- `backend/migrations/055_fix_kit_stock_unique_index.sql`
- `backend/migrations/056_unify_platform_codes.sql`

# 通用智能工具注册 — 提取分析引擎技术方案

## Context

构建通用工具注册框架的**核心价值**不在 CRUD 框架，而在于三个提取分析引擎：
1. **DocParser** — 从 API 文档自动提取 Schema
2. **RuleExtractor** — 从文档+样本中提取使用规则
3. **DataProfiler** — 从真实数据中提取用户画像 + 字段映射表

以快麦 ERP 为第一个验证 case，迁移现有硬编码 → DB 驱动。

---

## 〇、核心查询范式：宽泛查询 + 本地计算

### 问题

现有模式让 AI **精确构造 API 参数**去匹配用户意图。为此写了 145 行路由规则教 AI 怎么传参，仍然经常出错。
根因：我们不可能穷举所有参数组合，API 的过滤能力也有限。

### 新范式

```
精确匹配（现在）                     宽泛查询 + 本地计算（新）
───────────                         ─────────────────────
AI 判断传 keyword 还是 outer_id      AI 只管调 product_list 宽泛拿数据
AI 判断 timeType 选哪个              code_execute 沙盒做精确过滤/统计
AI 传错参数 → 结果不对               代码过滤 → 结果精确
需要 145 行路由规则                  只需 ~20 行分类级规则
```

### 对各引擎的影响

| 引擎 | 精确匹配模式 | 宽泛查询模式 |
|------|------------|------------|
| **RuleExtractor** | 需提取每个参数的用法（70个action × N参数） | 只需提取分类级规则（7个分类怎么选） |
| **DataProfiler** | 画像仅注入提示词 | 画像 = **字段映射表**，代码过滤依赖它 |
| **PromptBuilder** | 145行参数级规则 | ~20行 + 字段映射 |
| **AI 大脑** | 学会精确传参 | 学会"调哪个 API + 写过滤代码" |

### 查询模式决策树

```
用户查询
    ↓
判断类型：
    ├─ 精确查询（单个订单号/编码）→ 直接传 order_id/outer_id，无需沙盒
    ├─ 模糊查询（名称/前缀/关键词）→ 宽泛查 keyword + 沙盒过滤
    └─ 统计类（"今天多少单""各店铺销量"）→ 宽泛查 + 沙盒聚合计算
```

### 沙盒代码示例

AI 大脑生成的沙盒代码（已有 `code_execute` + `execute_raw()` 基础设施）：

```python
# 用户问："TJ开头的商品有多少库存"
# AI 生成以下沙盒代码：

# Step 1: 宽泛查商品
products = await erp.query("product_list", {"keyword": "TJ", "page_size": 200})
# Step 2: 本地精确过滤
tj_products = [p for p in products["list"] if p["outerCode"].startswith("TJ")]
# Step 3: 查这些商品的库存
for p in tj_products:
    stock = await erp.query("stock_status", {"outer_id": p["outerCode"]})
    p["stock"] = stock.get("totalQty", 0)
# Step 4: 汇总
total = sum(p["stock"] for p in tj_products)
print(f"TJ开头共{len(tj_products)}个商品，总库存{total}")
```

**关键**：AI 要写出 `p["outerCode"]` 而不是 `p["code"]`，这就需要 DataProfiler 提供的**字段映射表**。

---

## 一、输入分析

### 快麦 API 文档格式（已爬取为 MD）

**主要输入**：`docs/kuaimai_api_summary.md`（5288行，精简版）

每个 API 的格式高度规律：
```markdown
## trade                          ← 分类

### 订单查询                       ← API 中文名
method: erp.trade.list.query      ← API 方法名
params:
请求参数​
全部展开
参数名                            ← 表头（固定）
类型
描述
必填
[默认值]                          ← 可选列
sid                               ← 参数名
string                            ← 类型
系统订单号，多个逗号隔开            ← 描述
tid                               ← 下一个参数...
string
平台订单号，多个逗号隔开
pageSize
integer
每页多少条，最大支持200，不能小于2
```

每个参数占 3-5 行：name → type → description → [必填] → [默认值]

**补充输入**：`docs/document/TECH_快麦API文档_完整.md`（38490行，含响应示例+错误码）

---

## 二、DocParser — 文档解析引擎

### 策略：结构化解析 + AI 增强

**80% 正则解析**（确定性高、零成本）+ **20% AI 补充**（推断 param_map、分类归属）

### 2.1 结构化解析器（纯 Python，无 LLM 调用）

```python
# backend/services/tool_registry/doc_parser.py

class DocParser:
    """API 文档解析引擎"""

    def parse_summary_doc(self, md_text: str) -> list[ParsedAction]:
        """解析 kuaimai_api_summary.md 格式的文档

        Returns: 结构化的 action 列表
        """
        actions = []
        current_category = ""

        for block in self._split_api_blocks(md_text):
            action = self._parse_single_api(block, current_category)
            if action:
                actions.append(action)

        return actions

    def _split_api_blocks(self, text: str) -> list[str]:
        """按 '### API名' 分割文档为独立 API 块

        识别模式：
        - '## xxx' → 分类切换 (trade/basic/product...)
        - '### xxx' → 新 API 开始
        - 'method: xxx' → API 方法名
        """

    def _parse_single_api(self, block: str, category: str) -> ParsedAction | None:
        """解析单个 API 块 → ParsedAction

        提取：
        1. display_name: '### ' 后面的文本
        2. api_method: 'method: ' 后面的文本
        3. params: 逐行解析参数组
        4. response_fields: 解析响应字段列表（见 2.1.1）
        """

    def _parse_response_fields(self, block: str) -> list[ParsedResponseField]:
        """解析响应字段（从完整文档的响应参数表格）

        识别模式：
        - '返回参数' / '响应参数' 段落后的字段列表
        - 每个字段: name → type → description（与请求参数同格式）

        输出: ParsedResponseField 列表，后续供 ResponseFormatter 使用
        """

    def _parse_params(self, lines: list[str]) -> list[ParsedParam]:
        """核心：参数解析状态机

        状态机：
        EXPECT_NAME → EXPECT_TYPE → EXPECT_DESC → EXPECT_FLAG → EXPECT_NAME

        难点：
        1. '必填'/'默认值' 是可选行 → 需要判断下一行是新参数名还是标记
        2. 描述可能跨多行 → 如果下一行不是已知类型名，则追加到描述
        3. 表头行 ('参数名','类型','描述','必填','默认值','全部展开') 要跳过

        判断规则：
        - 已知类型集合: {string, integer, int, long, float, double, boolean, array, object, Long, String, Integer}
        - 如果当前在 EXPECT_TYPE 状态且行不在已知类型集合 → 说明上一行不是参数名，回退
        - '必填' 单独成行 → 标记 required=True
        - 其他单独成行（非类型、非空）→ 可能是默认值
        """

@dataclass
class ParsedParam:
    name: str               # API 原始参数名 (如 "tid", "pageSize")
    param_type: str         # string/integer/array/object
    description: str        # 中文描述
    required: bool          # 是否必填
    default_value: str      # 默认值 (如有)
    semantic_type: str      # 语义类型 (见第 4.5 节参数类型系统)

@dataclass
class ParsedResponseField:
    name: str               # API 响应字段名 (如 "tid", "buyerNick")
    field_type: str         # string/integer/long/array/object
    description: str        # 中文描述 (如 "平台订单号")

@dataclass
class ParsedAction:
    category: str           # trade/basic/product...
    display_name: str       # 中文名 (如 "订单查询")
    api_method: str         # 方法名 (如 "erp.trade.list.query")
    params: list[ParsedParam]
    response_fields: list[ParsedResponseField]  # ← 新增：响应字段列表
    raw_text: str           # 原始文本 (供 AI 增强用)
```

### 2.2 AI 增强层（LLM 调用，处理正则无法解决的部分）

结构化解析完成后，AI 负责：

**任务 A：生成 param_map（用户友好名 → API 参数名）**

这是现在手写在 `registry/trade.py` 里的：
```python
# 现在手写：
param_map = {"order_id": "tid", "system_id": "sid", "buyer": "buyerNick"}
```

AI Prompt：
```
你是 API 参数映射专家。给定以下 API 参数列表，为每个参数生成一个用户友好的英文别名。

规则：
1. 别名用 snake_case，简短有意义
2. 中文业务用户会用别名查询，所以要直觉化
3. 已有 API 参数名足够友好的（如 status, code），别名等于参数名本身
4. 保留原始参数名作为 key，别名作为 value

API: erp.trade.list.query (订单查询)
参数：
- tid (string): 平台订单号，多个逗号隔开
- sid (string): 系统订单号，多个逗号隔开
- buyerNick (string): 买家昵称
- timeType (string): 查询的时间类型
- startTime (string): 起始时间
- endTime (string): 截止时间
- userIds (string): 店铺ID，多个逗号隔开

输出 JSON：
{"tid": "order_id", "sid": "system_id", "buyerNick": "buyer", "timeType": "time_type", ...}
```

**任务 B：判断写操作**
```
以下 API 哪些是写操作（新增/修改/删除），哪些是查询操作？
只返回写操作的 method 列表。

API列表：
- erp.trade.list.query: 订单查询
- erp.trade.create: 创建系统手工单
- erp.customer.create: 新增修改客户基本信息
- ...
```

**任务 C：生成 param_docs（给 AI 看的参数文档）**

这是现在手写在 `ApiEntry.param_docs` 里的：
```python
# 现在手写：
param_docs = {
    "order_id": "平台订单号（淘宝18位/抖音19位...）支持多个逗号隔开",
}
```

AI Prompt：
```
你是 ERP 系统专家。为以下参数生成面向 AI 的使用文档。

要求：
1. 保留原始描述的核心信息
2. 补充实际使用时的格式示例
3. 如果描述中有枚举值，整理为清晰的列表
4. 如果有隐含约束，显式指出

参数：timeType (string)
原始描述：查询的时间类型：[created:下单时间]--[pay_time:付款时间]--[consign_time:发货时间]--[audit_time:审核时间]--[upd_time:修改时间]；与startTime、endTime同时使用，时间跨度建议不超过一天。为空时，默认为修改时间

期望输出：
"时间类型（可选值: created=下单时间, pay_time=付款时间, consign_time=发货时间, audit_time=审核时间, upd_time=修改时间）。需与 start_time/end_time 同时使用。默认按修改时间查。时间跨度建议≤1天"
```

**任务 E：生成响应格式化配置（labels + transforms + skip）**

这是 Phase 5B 手写在各 `formatters/*.py` 里的：
```python
# 现在手写：
_ORDER_LABELS = {"tid": "订单号", "buyerNick": "买家", "payment": "付款金额", ...}
_ORDER_TRANSFORMS = {"payment": lambda v: f"¥{v}", "created": format_timestamp, ...}
```

AI Prompt：
```
你是 ERP 数据展示专家。根据以下 API 响应字段列表，生成格式化配置。

API: erp.trade.list.query (订单查询)
响应字段：
- tid (string): 平台订单号
- sid (string): 系统订单号
- buyerNick (string): 买家昵称
- payment (string): 实付金额
- status (integer): 订单状态 (0:普通 7:合并 8:拆分)
- created (long): 创建时间（毫秒时间戳）
- picPath (string): 商品图片路径

请输出 JSON：
{
  "labels": {"tid": "订单号", "sid": "系统单号", "buyerNick": "买家", "payment": "实付金额", ...},
  "transforms": {
    "payment": "money",
    "created": "timestamp",
    "status": {"0": "普通", "7": "合并", "8": "拆分"}
  },
  "skip": ["picPath"],
  "nested_keys": ["orders"]
}

transforms 类型说明：
- "money" → 自动加 ¥ 前缀
- "timestamp" → 毫秒时间戳转可读日期
- "boolean" → 转为 是/否
- {...} → 枚举映射（值→中文）
```

**任务 D：推断参数语义类型（semantic_type）**

这是参数类型系统（见第 4.5 节）的核心输入。为每个参数标注语义类型，运行时自动获得对应的智能处理能力。

AI Prompt：
```
你是 API 参数分析专家。为以下参数标注语义类型。

可选类型：
- product_code: 商品编码（商家编码/货号/SKU编码/条码等）
- order_number: 订单号（平台订单号/系统单号/ERP单号等）
- date_range: 日期时间（起始/结束时间）
- pagination: 分页参数（页码/每页条数）
- entity_id: 实体ID（店铺ID/仓库ID/分类ID等）
- enum_value: 枚举值（状态/类型等有固定可选值的参数）
- text_search: 文本搜索（关键词/名称/昵称等模糊查询）
- generic: 以上都不是

API: erp.trade.list.query (订单查询)
参数：
- tid (string): 平台订单号，多个逗号隔开
- sid (string): 系统订单号，多个逗号隔开
- timeType (string): 查询的时间类型
- startTime (string): 起始时间
- pageSize (integer): 每页多少条
- userIds (string): 店铺ID，多个逗号隔开
- status (string): 订单状态

输出 JSON：
{"tid": "order_number", "sid": "order_number", "timeType": "enum_value", "startTime": "date_range", "pageSize": "pagination", "userIds": "entity_id", "status": "enum_value"}
```

### 2.3 解析流程

```
kuaimai_api_summary.md
    ↓
_split_api_blocks() → 70+ API 块
    ↓
_parse_single_api() × 70 → 70+ ParsedAction (纯正则, ~0.5秒)
    ↓
AI 增强 (分批, 每批 10 个 API):
    ├─ 任务A: generate_param_maps() → param_map
    ├─ 任务B: classify_write_ops() → is_write
    ├─ 任务C: enrich_param_docs() → param_docs
    ├─ 任务D: infer_semantic_types() → semantic_type (见 4.5 节)
    └─ 任务E: generate_formatter_config() → labels/transforms/skip (见 4.6 节)
    ↓
合并 → 写入 tool_actions 表 + tool_formatter_configs 表
```

**AI 调用量预估**：70个 API / 10 每批 = 7 次 AI 调用 × 5 任务 = ~35 次 qwen-plus 调用，总成本 < ¥2

---

## 三、RuleExtractor — 规则提取引擎

### 3.1 规则重心转移

在「宽泛查询 + 本地计算」范式下，规则不再需要教 AI "每个参数怎么传"，而是聚焦于：

| 规则层级 | 目的 | 举例 |
|---------|------|------|
| **分类路由** | 用户意图 → 哪个 API | "库存问题 → stock_status 或 product_list" |
| **查询模式** | 何时精确查、何时宽泛查+沙盒 | "单个订单号 → 直传；统计类 → 宽泛查+code_execute" |
| **字段语义** | API 返回字段的业务含义 | "outerCode=商家编码，barCode=条码，goodsNo=款号" |
| **参数约束** | API 调用的硬性限制 | "pageSize 最小2最大200" |
| **枚举映射** | 中文→系统值 | "待发货→WAIT_SEND_GOODS" |
| **错误码** | 错误含义 + 自修复 | "20002 → pageSize 不正确" |

**不再需要的规则**（由沙盒代码替代）：
- ~~"tid 和 sid 怎么选"~~ → 沙盒代码直接用字段名过滤
- ~~"timeType 选 created 还是 pay_time"~~ → 宽泛查时间范围，沙盒按需过滤
- ~~"先查商品再查库存"~~ → 沙盒代码直接编排多步调用

### 3.2 两层提取

**Layer A：正则提取（从已解析的参数描述中，无 LLM）**

```python
class RuleExtractor:
    def extract_from_parsed_actions(self, actions: list[ParsedAction]) -> list[ExtractedRule]:
        """从结构化解析结果中提取规则"""
        rules = []
        for action in actions:
            for param in action.params:
                rules.extend(self._extract_constraints(action, param))
                rules.extend(self._extract_enum_mappings(action, param))
                rules.extend(self._extract_defaults(action, param))
        return rules

    def _extract_constraints(self, action, param) -> list[ExtractedRule]:
        """正则模式：
        - r'最大支持(\d+)' → max_value
        - r'不能小于(\d+)' → min_value
        - r'与(.+)同时使用' → dependency
        - r'格式[：:](.+?)(?=[，。]|$)' → format_hint
        """

    def _extract_enum_mappings(self, action, param) -> list[ExtractedRule]:
        """匹配模式：
        - '[created:下单时间]--[pay_time:付款时间]' → 枚举映射
        - '0.停用 1.正常' → 数值枚举
        """
```

**Layer B：AI 推断（分类级规则 + 查询模式，按分类分批）**

AI Prompt：
```
你是 ERP 数据查询专家。分析以下 API 列表，生成分类级路由规则和查询模式建议。

## trade 分类（订单/交易相关）：
1. 订单查询 (erp.trade.list.query) - 参数: sid, tid, timeType, startTime, endTime, status...
2. 订单详情 (erp.trade.detail.query) - 参数: sid
3. 出库查询 (erp.trade.outstock.query) - 参数: sid, tid...
4. 物流查询 (erp.express.query) - 参数: sid, expressNo...

请输出 JSON：
{
  "routing_rules": [
    "用户问订单相关 → trade 分类",
    "用户问物流/快递 → express_query"
  ],
  "query_patterns": [
    {"scene": "查某个订单", "mode": "precise", "action": "order_list", "key_param": "tid"},
    {"scene": "今天多少单", "mode": "broad_then_compute", "action": "order_list", "broad_params": {"timeType": "created", "startTime": "today"}, "compute": "count(results)"},
    {"scene": "各店铺销量", "mode": "broad_then_compute", "action": "order_list", "compute": "group_by(shopName).count()"}
  ]
}
```

### 3.3 响应字段语义提取（新增，关键！）

宽泛查询后沙盒代码要知道字段含义，才能正确过滤。这需要从 API 响应示例或真实数据中提取。

```python
    async def extract_field_semantics(self, tool_id: str, samples: dict[str, list]) -> list[ExtractedRule]:
        """从样本数据中提取响应字段语义

        输入: {"order_list": [{"tid": "123", "sid": "456", "buyerNick": "张三", ...}]}

        输出规则 (rule_type='field_semantic'):
        - "order_list 响应字段: tid=平台订单号, sid=系统单号, buyerNick=买家昵称, status=订单状态, payment=实付金额"
        - "product_list 响应字段: outerCode=商家编码, name=商品名称, barCode=条码, totalQty=库存数量"
        """
```

AI Prompt：
```
以下是 ERP API order_list 返回的一条样本数据：
{
  "tid": "3248751029571832",
  "sid": "XS2403150001",
  "buyerNick": "张三",
  "status": "WAIT_SEND_GOODS",
  "payment": "128.00",
  "shopName": "天际旗舰店",
  "warehouseName": "杭州主仓",
  "outerCode": "TJ-0001",
  "skuOuterCode": "TJ-0001-WH-XL",
  "goodsName": "纯棉圆领T恤",
  "num": 2,
  "totalFee": "256.00",
  "created": "2026-03-15 14:23:01"
}

请为每个字段生成中文语义说明（JSON格式）：
{"tid": "平台订单号", "sid": "系统单号(ERP内部编号)", "buyerNick": "买家昵称", ...}
```

**这些字段语义存入 tool_rules (rule_type='field_semantic')，注入提示词后，AI 写沙盒代码时就知道用 `p["outerCode"]` 而不是 `p["code"]`。**

### 3.4 错误码提取（从完整文档，正则）

```python
    def extract_error_codes(self, full_doc_text: str) -> list[ExtractedRule]:
        """从完整文档中提取错误码表

        匹配模式: '| 错误码 | 错误信息 | 解决方案 |'
        分两类：
        1. 全局错误码（"API错误码解释" 章节）
        2. 每个 API 独有的错误码（各 API 的 "错误码解释" 小节）
        """
```

### 3.5 提取流程

```
已解析的 ParsedAction 列表 (来自 DocParser)
    ↓
Layer A: 正则提取 (~0.1秒)
    ├─ constraints: "pageSize 最大200，不能小于2"
    ├─ enum_mappings: "timeType: created=下单时间, pay_time=付款时间..."
    └─ defaults: "timeType 默认为修改时间"
    ↓
Layer B: AI 推断 (按分类分批, ~7次 AI 调用)
    ├─ routing_rules: "库存问题 → product 分类"
    ├─ query_patterns: "统计类 → broad_then_compute"
    └─ field_semantics: "outerCode=商家编码, barCode=条码"  ← 新增关键
    ↓
错误码提取 (正则, 从完整文档)
    ├─ 全局: {1: "服务不可用", 25: "签名无效", ...}
    └─ 每API: {20002: "pageSize不正确", ...}
    ↓
合并去重 → 写入 tool_rules 表
```

---

## 四、DataProfiler — 数据画像 + 字段映射引擎

### 4.0 核心定位变化

在「宽泛查询 + 本地计算」范式下，DataProfiler 不仅是"让 AI 更懂用户"的锦上添花，
而是**沙盒代码能正确运行的必要前提**：

```
没有画像：AI 写 p["code"] → 实际字段是 p["outerCode"] → 代码报错
有画像：  AI 知道编码字段叫 outerCode → 写出正确代码 → 结果准确
```

画像输出两部分：
1. **用户特征**（注入提示词）：编码规律、店铺列表、品类分布
2. **字段映射表**（注入提示词，供沙盒代码引用）：每个 API 返回的字段名 → 业务含义

### 4.1 采样策略

```python
class DataProfiler:
    """用户数据画像 + 字段映射生成器"""

    PROFILE_ACTIONS = [
        ("product_list", "商品编码+名称规律+响应字段"),
        ("sku_list", "SKU编码+规格命名+响应字段"),
        ("shop_list", "店铺列表"),
        ("warehouse_list", "仓库列表"),
        ("brand_list", "品牌列表"),
        ("cat_list", "分类列表"),
        ("order_list", "订单字段结构"),     # ← 新增：为沙盒代码提供订单字段映射
    ]

    async def profile(self, tool_id: str, user_id: str, dispatcher) -> dict:
        """完整画像流程

        1. 从 tool_actions 中获取可用的 list 类 actions
        2. 并发调用（最多 7 个，每个拉 1 页）
        3. 分析样本 → 用户特征 + 字段映射
        4. 存入 tool_user_profiles
        """
```

### 4.2 分析模块

**模块 A：编码规律分析（正则 + 统计，无 LLM）**

```python
    def _analyze_encoding_patterns(self, items: list[dict], field_name: str) -> dict:
        """分析编码字段的命名规律

        输入：50 个商品的 outerCode 值
        如: ["TJ-0001", "TJ-0002", "BK-1001"]

        分析步骤：
        1. 提取前缀: Counter(prefix) → {"TJ": 30, "BK": 20}
        2. 检测格式: 正则匹配 → "字母前缀-4位数字"
        3. 分隔符: 检测 -, _, . 等

        输出:
        {
            "pattern": "前缀-4位数字",
            "prefixes": {"TJ": "60%", "BK": "40%"},
            "examples": ["TJ-0001", "BK-1001"]
        }
        """
```

**模块 B：枚举值发现（统计，无 LLM）**

```python
    def _discover_enums(self, items: list[dict]) -> dict:
        """发现高频枚举值

        对每个字段统计 unique 值:
        - unique_count < 20 → 枚举字段 → 输出完整列表
        如: {"杭州主仓": 50%, "义乌分仓": 30%, "广州仓": 20%}
        """
```

**模块 C：字段映射表 + 格式化配置生成（核心！）**

字段映射表同时服务两个目的：
1. **沙盒代码引用**：AI 写 `code_execute` 时知道用 `p["outerCode"]` 而非 `p["code"]`
2. **响应格式化配置**：自动生成 `_LABELS` + `_TRANSFORMS` + `_SKIP` 供 ResponseFormatter 使用

```python
    def _build_field_map(self, action_name: str, sample_items: list[dict]) -> dict:
        """从样本数据的 key 列表自动生成字段映射 + 格式化配置

        输入: order_list 返回的一条数据的所有 key:
        ["tid", "sid", "buyerNick", "status", "payment", "shopName",
         "warehouseName", "outerCode", "goodsName", "num", "created", ...]

        输出（AI 辅助生成中文语义）:
        {
            "order_list": {
                "tid": "平台订单号",
                "sid": "系统单号",
                "buyerNick": "买家昵称",
                "status": "订单状态(枚举值)",
                "payment": "实付金额(元)",
                "shopName": "店铺名称",
                "outerCode": "商家编码",
                "goodsName": "商品名称",
                "num": "购买数量",
                "created": "下单时间"
            }
        }
        """
```

这里 AI 的作用是**给字段名标注中文语义**（因为 `buyerNick`、`outerCode` 等缩写不直观），
但字段名本身是从真实数据中 100% 确定的——不是猜的。

**模块 C2：Transform 自动检测（统计 + 正则，无 LLM）**

从样本数据的**值**自动推断每个字段需要什么 transform，无需 AI：

```python
    def _detect_transforms(self, action_name: str, sample_items: list[dict]) -> dict:
        """从样本值自动检测字段的 transform 类型

        检测规则（优先级从高到低）：
        1. 时间戳: 值为 int/float 且 > 1e12 → "timestamp"
        2. 金额: 字段名含 price/amount/fee/money/cost → "money"
        3. 布尔: 值集合 ⊆ {0, 1, True, False} → "boolean"
        4. 枚举: unique 值 < 20 且为 int 类型 → "enum" (值列表由 AI 补中文)
        5. 跳过: 字段名匹配 pic/Path/Url/sysItemId/companyId → "skip"
        6. 嵌套: 值为 list[dict] → "nested" (记录子项字段名，递归检测)

        输出:
        {
            "created": "timestamp",
            "payment": "money",
            "isRefund": "boolean",
            "status": {"type": "enum", "values": [0, 1, 7, 8]},
            "picPath": "skip",
            "orders": {"type": "nested", "child_fields": ["sysTitle", "num", ...]}
        }
        """
```

**关键**: Phase 5B 手写的 42 个 `_TRANSFORMS` dict 中，80% 可以由这个模块自动检测：
- 时间戳字段（`created`, `payTime`, `consignTime` 等）→ 值 > 1e12，100% 准确
- 金额字段（`payment`, `totalAmount`, `refundMoney` 等）→ 字段名特征，95% 准确
- 枚举字段（`status`, `type`, `afterSaleType` 等）→ unique 值 < 20，90% 准确
- 只有枚举值的**中文标签**需要 AI 补充（如 `1 → "退款", 2 → "退货"`）

**模块 D：AI 总结（最终汇总）**

```python
    async def _summarize_with_ai(self, raw_analysis: dict, field_maps: dict) -> dict:
        """AI 汇总分析结果 → 生成画像 + 字段映射"""
```

AI Prompt：
```
你是数据分析师。根据以下 ERP 样本分析结果，生成用户数据画像和字段映射表。

## 样本分析结果
商品数据（50条）：
- 编码字段: outerCode, 前缀分布: TJ=60%, BK=40%, 格式: 字母-4位数字
- 名称字段: name, 示例: "纯棉圆领T恤 白色"

店铺列表: [{"id":101,"name":"天际旗舰店"},{"id":102,"name":"天际拼多多店"}]
仓库列表: [{"id":1,"name":"杭州主仓"},{"id":2,"name":"义乌分仓"}]

## API 响应字段（从真实数据提取的 key）
product_list: ["outerCode","name","barCode","goodsNo","catName","brandName","status","totalQty","created"]
order_list: ["tid","sid","buyerNick","status","payment","shopName","warehouseName","outerCode","goodsName","num","created"]

请输出 JSON（≤300字）：
{
  "user_profile": {
    "encoding_patterns": {"商品编码": "TJ/BK + 连字符 + 4位数字"},
    "entity_lists": {"店铺": [...], "仓库": [...]},
    "statistics": {"商品总数": 2156},
    "business_type": "服装，多平台"
  },
  "field_maps": {
    "product_list": {"outerCode": "商家编码", "name": "商品名称", "barCode": "条码", "totalQty": "库存数量", ...},
    "order_list": {"tid": "平台订单号", "sid": "系统单号", "buyerNick": "买家", "payment": "实付金额", ...}
  }
}
```

### 4.3 画像注入方式

画像存入 DB 后，在对话时注入到 system_prompt 的**两个位置**：

**位置 1：用户特征（给 AI 理解用户）**
```
用户数据特征：
- 商品编码格式：TJ/BK + 连字符 + 4位数字，如 TJ-0001
- 店铺：天际旗舰店(天猫)、天际拼多多店、天际抖音小店
- 仓库：杭州主仓、义乌分仓
- 商品约2156个，服装类目为主
```

**位置 2：字段映射表（给 AI 写沙盒代码时参考）**
```
ERP API 字段映射（写 code_execute 代码时使用）：
- product_list: outerCode=商家编码, name=商品名称, barCode=条码, totalQty=库存
- order_list: tid=平台订单号, sid=系统单号, buyerNick=买家, payment=实付金额, shopName=店铺
- sku_list: skuOuterCode=SKU编码, spec=规格, price=单价
```

### 4.4 画像完整流程

```
触发：用户首次绑定 ERP（或手动"刷新画像"）
    ↓
选择采样 actions（list 类、非写操作）
    ↓
并发调用 API（每个 action 拉 1 页, page_size=50）
    ↓
分析（正则+统计，无 LLM）:
    ├─ 编码规律（前缀/格式/分隔符）
    ├─ 枚举发现（仓库/店铺/分类/品牌完整列表）
    ├─ 数据量级（total 字段）
    └─ 字段名提取（每个 API 响应的所有 key）
    ↓
AI 汇总（1次 qwen-plus 调用）:
    ├─ 用户特征画像（≤200字）
    └─ 字段映射表（每个 API 的 key→中文语义）
    ↓
存入 tool_user_profiles 表
    ↓
对话时注入 system_prompt（用户特征 + 字段映射）
    ↓
效果：AI 写沙盒代码时自动使用正确的字段名
```

---

## 4.5、ParamTypeEngine — 参数类型系统

### 问题

当前 `param_mapper.py` 和 `param_guardrails.py` 中的运行时智能全是硬编码：

```python
# param_mapper.py — 同义参数兜底（硬编码）
_PARAM_SYNONYMS = {"sku_outer_id": "outer_id", "outer_id": "sku_outer_id", ...}

# param_guardrails.py — 编码宽泛查询（硬编码）
def extract_base_code(code): ...  # DBTXL01-02 → DBTXL

# param_mapper.py — 日期补全（硬编码）
def _normalize_dates(params): ...  # 2026-03-01 → 2026-03-01 00:00:00

# param_guardrails.py — 订单号格式校验（硬编码）
def _correct_order_param(): ...  # 16位数字 order_id → system_id
```

**客户自注册 API 时，这些能力不会自动生效。** 客户不会编程，不可能手写 synonym 映射或 base_code 提取逻辑。

### 解决方案：语义类型驱动的运行时智能

每个参数标注一个 `semantic_type`，运行时按类型自动套用对应的处理逻辑。客户只需选类型（或由 DocParser AI 自动推断），不需要写代码。

### 类型定义与内置能力

| semantic_type | 内置运行时能力 | 来源（现有硬编码） |
|---------------|--------------|-------------------|
| `product_code` | 同义兜底（outer_id ↔ sku_outer_id）+ 编码宽泛查询（extract_base_code + 匹配）+ 大小写不敏感 | `_PARAM_SYNONYMS` + `broadened_code_query` |
| `order_number` | 格式校验互转（16位数字 order_id → system_id） | `_correct_order_param` |
| `date_range` | 自动补全时分秒（start → 00:00:00, end → 23:59:59） | `_normalize_dates` |
| `pagination` | pageNo/pageSize 标准化 + 最小值强制（≥20） | `map_params` 分页逻辑 |
| `entity_id` | 中文别名解析（店铺→shop_ids, 仓库→warehouse_id） | `PARAM_ALIASES` |
| `enum_value` | 中文→系统值映射（待发货→WAIT_SEND_GOODS） | `RuleExtractor` 枚举规则 |
| `text_search` | 无特殊处理，直接透传 | — |
| `generic` | 无特殊处理，直接透传 | — |

### 类型检测方式（三层）

```
Layer 1: 正则规则（零成本，DocParser 解析时即可）
    ├─ 参数名含 start/end/time/date → date_range
    ├─ 参数名含 page/pageNo/pageSize → pagination
    └─ 参数名含 status/type + 描述含枚举值 → enum_value

Layer 2: AI 推断（DocParser 任务D，批量处理）
    ├─ 描述含"编码/货号/outer" → product_code
    ├─ 描述含"订单号/单号" → order_number
    ├─ 描述含"ID/店铺/仓库" → entity_id
    └─ 描述含"关键词/名称/昵称" → text_search

Layer 3: DataProfiler 验证（从真实数据确认）
    ├─ 字段值匹配编码格式（字母+数字+分隔符）→ 确认 product_code
    └─ 字段值唯一值<20 → 确认 enum_value
```

### 运行时处理流程

```python
# backend/services/tool_registry/param_type_engine.py

class ParamTypeEngine:
    """参数类型驱动的运行时处理器"""

    def preprocess(self, action_params: list[ActionParam], user_params: dict) -> tuple[dict, list[str]]:
        """API 调用前的参数预处理

        按每个参数的 semantic_type 应用对应的处理逻辑：
        1. product_code → 同义参数兜底
        2. order_number → 格式校验互转
        3. date_range → 补全时分秒
        4. pagination → 标准化 + 最小值
        5. entity_id → 别名解析
        """

    def postprocess(self, action_params: list[ActionParam], user_params: dict,
                    data: dict, client) -> tuple[dict, str]:
        """API 调用后的结果增强（零结果时触发）

        按参数类型决定兜底策略：
        1. product_code → 编码宽泛查询（extract_base_code + 匹配）
        2. order_number → 诊断建议（换参数重试）
        3. 其他类型 → 不干预
        """
```

### 客户自注册体验

```
客户注册新 API 时的参数配置界面：

参数名: outerId
类型: string
描述: 商家编码
语义类型: [商品编码 ▼]  ← 下拉选择（或 AI 自动推荐）
         ├─ 商品编码        → 自动获得: 同义兜底 + 编码宽泛查询
         ├─ 订单号          → 自动获得: 格式校验 + 互转
         ├─ 日期范围        → 自动获得: 时分秒补全
         ├─ 分页            → 自动获得: 标准化
         ├─ 实体ID(店铺/仓库) → 自动获得: 别名解析
         ├─ 枚举值          → 自动获得: 中文→系统值映射
         ├─ 文本搜索        → 直接透传
         └─ 通用            → 直接透传
```

**关键**：客户选了"商品编码"，就自动获得 `sku_outer_id ↔ outer_id` 同义兜底、`DBTXL01-02 → DBTXL` 编码拆分宽泛查询等所有能力，不需要写一行代码。

### 与现有模块的关系

```
DocParser (任务D)          → 推断 semantic_type → 存入 tool_actions
DocParser (任务E)          → 生成 labels/transforms/skip → 存入 tool_formatter_configs
RuleExtractor              → 提取枚举映射 → enum_value 类型运行时使用
DataProfiler               → 验证/修正 semantic_type + 提取编码规律 + 检测 transforms
ResponseFormatter          → 运行时从 DB 加载配置 → format_item_with_labels 格式化
ParamTypeEngine            → 运行时按 semantic_type 执行预处理/后处理
UniversalDispatcher        → ParamTypeEngine.preprocess → API → ResponseFormatter.format
```

### 迁移路径

现有硬编码 → ParamTypeEngine 的迁移：

| 现有代码 | 迁移到 | 触发条件 |
|---------|--------|---------|
| `param_mapper._PARAM_SYNONYMS` | `ParamTypeEngine.preprocess` | `semantic_type == "product_code"` |
| `param_guardrails._correct_order_param` | `ParamTypeEngine.preprocess` | `semantic_type == "order_number"` |
| `param_mapper._normalize_dates` | `ParamTypeEngine.preprocess` | `semantic_type == "date_range"` |
| `param_mapper` 分页逻辑 | `ParamTypeEngine.preprocess` | `semantic_type == "pagination"` |
| `param_mapper.PARAM_ALIASES` | `ParamTypeEngine.preprocess` | `semantic_type == "entity_id"` |
| `param_guardrails.broadened_code_query` | `ParamTypeEngine.postprocess` | `semantic_type == "product_code"` |
| `param_guardrails.diagnose_empty_result` | `ParamTypeEngine.postprocess` | `semantic_type == "order_number"` |
| `formatters/*._LABELS` | `tool_formatter_configs.labels` | 所有 action |
| `formatters/*._TRANSFORMS` | `tool_formatter_configs.transforms` | 所有 action |
| `formatters/*._SKIP` | `tool_formatter_configs.skip` | 所有 action |
| `formatters/common.format_item_with_labels` | `ResponseFormatter._build_transforms + format` | 所有 action |

---

## 4.6、ResponseFormatter — 响应格式化引擎

### 问题（Phase 5B 实证）

Phase 5B 手动重写 42 个 formatter，核心工作量：
- 逐个 API 交叉比对文档，修正 **22 处字段名错误**（如 `refundId` → API 实际是 `id`）
- 补全 **30+ 遗漏的高价值字段**（如采购在途、退款金额、收件人信息）
- 为 **60+ 字段**手写 transform（时间戳→日期、状态码→中文、金额→¥）
- 修复 **3 处嵌套结构不匹配**（如维修单详情 `{order, itemList, feeList}` 结构）

**客户自注册 API 时不可能手写 42 个 formatter。** 需要自动化。

### 解决方案：Phase 5B 模式 + 自动生成配置

Phase 5B 已经建好了**运行时引擎**：`format_item_with_labels(item, labels, skip, transforms)`。
缺的是**自动生成配置**（labels/transforms/skip）。

### 配置来源（三层叠加）

```
Layer 1: DocParser 任务E — 从 API 文档的响应字段解析
    ├─ labels: 字段名 → 中文描述（文档中 100% 有）
    ├─ skip: 图片/内部ID 字段名模式匹配
    └─ transforms: 根据字段类型推断（long=时间戳、含"金额"=money）

Layer 2: DataProfiler 模块C2 — 从真实数据样本验证+补充
    ├─ transforms 验证: 检查值是否真的是时间戳/金额（避免误判）
    ├─ enum 发现: unique值<20 的字段自动标记为枚举
    ├─ nested 检测: 值为 list[dict] 的字段标记为嵌套
    └─ skip 补充: 所有样本中值全为 null/空 的字段自动跳过

Layer 3: AI 精化（1次调用） — 枚举值中文标签
    └─ 枚举映射: {0: "普通", 1: "退款", 2: "退货"} ← AI 根据上下文生成中文
```

### 配置存储（DB 表）

```sql
CREATE TABLE tool_formatter_configs (
    id BIGINT PRIMARY KEY,
    tool_id UUID REFERENCES tool_registrations(id),
    action_name TEXT NOT NULL,           -- "order_list"
    labels JSONB NOT NULL,               -- {"tid": "订单号", "buyerNick": "买家", ...}
    transforms JSONB DEFAULT '{}',       -- {"created": "timestamp", "payment": "money", "status": {...}}
    skip TEXT[] DEFAULT '{}',            -- ["picPath", "sysItemId"]
    nested_keys JSONB DEFAULT '{}',      -- {"orders": {"labels": {...}, "transforms": {...}}}
    response_key TEXT,                   -- "list" / "stockStatusVoList" / "trades"
    empty_message TEXT,                  -- "未找到订单"
    config_source TEXT DEFAULT 'auto',   -- auto / manual / ai_enhanced
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tool_id, action_name)
);
```

### 运行时流程

```python
# backend/services/tool_registry/response_formatter.py

class ResponseFormatter:
    """通用响应格式化器 — 运行时从 DB 加载配置"""

    async def format(self, action_name: str, data: dict, tool_id: str) -> str:
        """格式化 API 响应

        1. 从 DB/缓存 加载 formatter_config
        2. 提取列表数据: data[response_key]
        3. 对每条记录调用 format_item_with_labels(item, labels, skip, transforms)
        4. 处理嵌套结构: nested_keys 中的子列表递归格式化
        5. 拼接 header + items + pagination

        Returns: 格式化后的文本（与 Phase 5B 输出格式一致）
        """
        config = await self._get_config(tool_id, action_name)
        if not config:
            return format_generic_list(data, ...)  # 兜底：JSON 输出

        # 构建运行时 transforms
        runtime_transforms = self._build_transforms(config.transforms)

        items = data.get(config.response_key) or data.get("list") or []
        total = data.get("total", len(items))

        if not items:
            return config.empty_message or f"未找到{action_name}数据"

        lines = [f"共 {total} 条记录：\n"]
        for item in items[:20]:
            main = "- " + format_item_with_labels(
                item, config.labels, set(config.skip), runtime_transforms)

            # 嵌套结构处理
            for nested_key, nested_config in (config.nested_keys or {}).items():
                sub_items = item.get(nested_key) or []
                for sub in sub_items[:5]:
                    nested_transforms = self._build_transforms(nested_config.get("transforms", {}))
                    main += "\n    · " + format_item_with_labels(
                        sub, nested_config["labels"], transforms=nested_transforms)

            lines.append(main)

        if int(total) > len(items):
            lines.append(f"\n（显示前{len(items)}条，共{total}条）")
        return "\n".join(lines)

    def _build_transforms(self, transform_config: dict) -> dict[str, Callable]:
        """将 DB 中的 transform 类型 → 运行时函数

        类型映射：
        - "timestamp" → format_timestamp
        - "money" → lambda v: f"¥{v}" if v else ""
        - "boolean" → lambda v: "是" if v else "否"
        - {"0": "普通", "1": "退款"} → lambda v: mapping.get(v, str(v))
        """
        BUILTIN = {
            "timestamp": format_timestamp,
            "money": lambda v: f"¥{v}" if v else "",
            "boolean": lambda v: "是" if v else "否",
        }
        result = {}
        for field, spec in transform_config.items():
            if isinstance(spec, str) and spec in BUILTIN:
                result[field] = BUILTIN[spec]
            elif isinstance(spec, dict):
                mapping = spec
                result[field] = lambda v, m=mapping: m.get(str(v), str(v))
        return result
```

### 与现有 Phase 5B 代码的关系

```
Phase 5B 硬编码（现在）                    ResponseFormatter（迁移后）
─────────────────────                    ──────────────────────────
formatters/trade.py:                     tool_formatter_configs 表:
  _ORDER_LABELS = {...}                    labels = {"tid":"订单号",...}
  _ORDER_TRANSFORMS = {...}                transforms = {"created":"timestamp",...}
  _ORDER_SKIP = set()                      skip = ["picPath",...]
                                           nested_keys = {"orders":{...}}
formatters/common.py:                    response_formatter.py:
  format_item_with_labels()   ← 复用 →    format_item_with_labels()（同一函数）
```

Phase 5B 的 `format_item_with_labels` 不需要改，它是运行时引擎。
迁移只是把各 formatter 文件中的 `_LABELS`/`_TRANSFORMS` dict 搬到 DB。

### 客户自注册体验

```
客户注册新 API 后的格式化配置界面：

API: my_custom.order.query
响应字段（自动检测，可手动调整）：

字段名          中文标签         格式化          操作
──────────────────────────────────────────────────
order_no       订单号          [原样 ▼]         ✓ 显示
customer       客户名          [原样 ▼]         ✓ 显示
amount         金额           [金额(¥) ▼]      ✓ 显示
status         状态           [枚举映射 ▼]      ✓ 显示
                              ├ 1 → [待处理]
                              ├ 2 → [处理中]
                              └ 3 → [已完成]
created_at     创建时间        [时间戳 ▼]       ✓ 显示
pic_url        图片           [—]              ✗ 隐藏
internal_id    内部ID         [—]              ✗ 隐藏

格式化类型下拉：原样 / 金额(¥) / 时间戳 / 布尔(是否) / 枚举映射

[预览效果]
- 订单号: ORD001 | 客户名: 张三 | 金额: ¥199.00 | 状态: 待处理 | 创建时间: 2026-03-18 14:00
```

**关键**：80% 的配置由 Layer 1+2 自动生成，客户只需微调枚举中文标签和隐藏不需要的字段。

---

## 五、FeedbackLoop — 失败学习引擎

### 5.1 实时记录

```python
class FeedbackLoop:
    async def record(self, tool_id, action, params, success, error_code, error_msg, duration_ms):
        """每次 API 调用后 fire-and-forget 记录"""
        # 写入 tool_usage_logs 表
```

集成点：在 `UniversalDispatcher.execute()` 的 finally 块中调用。

### 5.2 失败分析（定时触发或按需）

```python
    async def analyze_failures(self, tool_id: str, since_hours: int = 24) -> list[dict]:
        """分析近期失败，发现新规则

        触发条件：同一 error_code 出现 ≥3 次

        流程：
        1. 从 tool_usage_logs 查询近期失败记录
        2. 按 (action, error_code) 聚合
        3. 对高频失败模式调用 AI 分析
        """
```

AI Prompt：
```
以下是 ERP API 近期的失败记录：

action=order_list, error_code=20002, 共失败5次
失败时的参数: [{"pageSize": 10}, {"pageSize": 5}, {"pageSize": 15}, ...]

请分析失败原因并生成修复规则：
{
  "analysis": "pageSize 低于最小值导致失败",
  "rule": {"type": "constraint", "rule_text": "pageSize 最小值为 20", "action": "order_list"},
  "auto_fix": {"param": "pageSize", "condition": "< 20", "fix_to": 20}
}
```

### 5.3 实时自修复

```python
    async def try_auto_fix(self, action: str, params: dict, error_code: str) -> dict | None:
        """查找已有的 auto_fix 规则，尝试修正参数

        从 tool_rules 中查找 rule_type='error_handling' 且有 auto_fix 的规则
        匹配 error_code → 应用修正 → 返回修正后的 params
        """
```

集成到 `UniversalDispatcher.execute()`：
```python
    result = await self._call_api(method, api_params)
    if not result.get("success"):
        fixed_params = await feedback.try_auto_fix(action, params, error_code)
        if fixed_params:
            result = await self._call_api(method, fixed_params)  # 自动重试
```

---

## 六、文件结构

```
backend/services/tool_registry/
├── __init__.py
├── models.py               # 数据类 (~80行)
├── doc_parser.py            # 文档解析引擎 (~300行)
│   ├── parse_summary_doc()     # 入口：解析精简文档
│   ├── _split_api_blocks()     # 按 ### 分割
│   ├── _parse_single_api()     # 解析单个 API
│   ├── _parse_params()         # 参数状态机（核心）
│   ├── enhance_with_ai()       # AI 增强层
│   │   ├── _generate_param_maps()   # 任务A: 生成 param_map
│   │   ├── _classify_write_ops()    # 任务B: 识别写操作
│   │   ├── _enrich_param_docs()     # 任务C: 增强参数文档
│   │   └── _infer_semantic_types()  # 任务D: 推断参数语义类型
│   └── save_to_db()            # 存入 tool_actions
├── rule_extractor.py        # 规则提取引擎 (~250行)
│   ├── extract_from_parsed()   # 正则层：约束/枚举/默认值
│   ├── extract_with_ai()       # AI层：操作链/决策/隐含约束
│   ├── extract_error_codes()   # 错误码提取
│   └── save_to_db()            # 存入 tool_rules
├── data_profiler.py         # 数据画像引擎 (~300行)
│   ├── profile()               # 入口：完整画像流程
│   ├── _select_sample_actions() # 选择采样目标
│   ├── _fetch_samples()         # 并发 API 采样
│   ├── _analyze_encoding()      # 编码规律分析
│   ├── _discover_enums()        # 枚举值发现
│   ├── _detect_transforms()     # Transform 自动检测（时间戳/金额/枚举/嵌套）
│   ├── _summarize_with_ai()     # AI 汇总
│   └── save_to_db()             # 存入 tool_user_profiles + tool_formatter_configs
├── response_formatter.py   # 响应格式化引擎 (~200行)
│   ├── format()                # 入口：从 DB 加载配置 + 格式化
│   ├── _get_config()           # 加载/缓存 formatter_config
│   ├── _build_transforms()     # DB 配置 → 运行时函数
│   └── _format_nested()        # 嵌套结构递归格式化
├── param_type_engine.py     # 参数类型运行时引擎 (~200行)
│   ├── preprocess()            # 按 semantic_type 预处理参数
│   ├── postprocess()           # 按 semantic_type 后处理结果（零结果兜底）
│   ├── _handle_product_code()  # 同义兜底 + 编码宽泛查询
│   ├── _handle_order_number()  # 格式校验互转
│   ├── _handle_date_range()    # 日期补全
│   ├── _handle_pagination()    # 分页标准化
│   └── _handle_entity_id()     # 别名解析
├── feedback_loop.py         # 失败学习引擎 (~200行)
│   ├── record()                 # 记录调用日志
│   ├── analyze_failures()       # 失败分析
│   └── try_auto_fix()           # 实时自修复
├── registry_service.py      # 注册 CRUD (~150行)
├── tool_builder.py          # 生成 FC 工具定义 (~150行)
├── prompt_builder.py        # 生成路由提示词 (~150行)
└── universal_dispatcher.py  # 通用调度器 (~200行)
```

---

## 七、集成点（与现有系统交互）

### 7.1 提示词注入

**位置**：`config/agent_tools.py:build_agent_system_prompt()` (L438)

```python
+ ERP_ROUTING_PROMPT         # 迁移后 → 从 tool_rules 动态生成
+ CRAWLER_ROUTING_PROMPT     # 暂不动
+ CODE_ROUTING_PROMPT        # 暂不动
```

### 7.2 画像注入（用户特征 + 字段映射）

**位置**：`services/agent_context.py:_build_system_prompt()` (L186)

在知识库注入后追加：
```python
profile = await get_user_tool_profile(self.user_id)
if profile:
    # 用户特征（给 AI 理解业务上下文）
    base_prompt += f"\n\n用户数据特征：\n{profile['user_profile']}"
    # 字段映射（给 AI 写 code_execute 沙盒代码时参考）
    base_prompt += f"\n\nERP API 字段映射（写 code_execute 代码时使用正确字段名）：\n{profile['field_maps']}"
```

### 7.2.1 查询模式引导

**位置**：`config/agent_tools.py` 的 `CODE_ROUTING_PROMPT` 或 `ERP_ROUTING_PROMPT` 中追加：

```python
QUERY_PATTERN_PROMPT = (
    "## 数据查询最佳实践\n"
    "- 精确查询（单个订单号/商品编码）→ 直接传参数调 ERP 工具\n"
    "- 模糊查询（名称/前缀/关键词）→ 宽泛查询 + code_execute 过滤\n"
    "- 统计类（多少单/各店铺销量/TOP10）→ 宽泛查询 + code_execute 聚合计算\n"
    "- 写 code_execute 代码时，参考「ERP API 字段映射」使用正确的字段名\n"
)
```

### 7.3 工具注册

**位置**：`config/agent_tools.py:build_agent_tools()` (L142)

从 DB 动态加载工具定义，替代 `build_erp_tools()`。

### 7.4 工具执行

**位置**：`services/tool_executor.py` (L40)

替代 `_erp_dispatch`，用 `UniversalDispatcher`。

### 7.5 响应格式化

**位置**：`UniversalDispatcher.execute()` 的返回前

```python
# 现在: dispatcher.py → get_formatter(entry.formatter) → formatter(data, entry)
# 迁移后: UniversalDispatcher → ResponseFormatter.format(action, data, tool_id)

raw_data = await self._call_api(method, api_params)
formatted = await self.response_formatter.format(action_name, raw_data, tool_id)
return formatted
```

替代现有 `formatters/__init__.py` 的 `get_formatter()` + 42 个硬编码 formatter 函数。

### 7.6 失败记录

**位置**：`UniversalDispatcher.execute()` finally 块

调用 `FeedbackLoop.record()`，复用现有 `knowledge_extractor` 的 fire-and-forget 模式。

---

## 八、ERP 迁移策略

### 一次性数据迁移脚本

```python
# backend/scripts/migrate_erp_to_registry.py

async def migrate():
    """将现有 registry/*.py 数据迁移到 tool_registry 表"""

    # 1. 创建 tool_registration
    tool_id = create("kuaimai_erp", ...)

    # 2. 遍历 TOOL_REGISTRIES → tool_actions
    for tool_name, registry in TOOL_REGISTRIES.items():
        for action_name, entry in registry.items():
            insert_action(tool_id, action_name, entry)

    # 3. 解析 ERP_ROUTING_PROMPT → tool_rules
    #    用正则拆分 145 行规则为独立条目

    # 4. 运行 DocParser 补充缺失信息
    #    （现有 ApiEntry 没有的：响应 schema、完整错误码）

    # 5. 迁移 formatter 配置 → tool_formatter_configs
    for module_name, formatters in ALL_FORMATTERS.items():
        for func_name, func in formatters.items():
            # 从 Phase 5B 的 _LABELS/_TRANSFORMS 常量提取配置
            labels = extract_labels_from_module(module_name, func_name)
            transforms = extract_transforms_from_module(module_name, func_name)
            insert_formatter_config(tool_id, func_name, labels, transforms)
```

### 迁移后删除的文件

```
backend/services/kuaimai/registry/  (整个目录)
backend/config/erp_tools.py  (build_erp_tools + ERP_ROUTING_PROMPT)
backend/services/kuaimai/dispatcher.py  (被 UniversalDispatcher 替代)
backend/services/kuaimai/param_doc.py  (被 UniversalDispatcher 替代)
```

### 迁移后删除的文件（补充）

```
backend/services/kuaimai/param_guardrails.py  (运行时智能迁移到 param_type_engine.py)
```

### 迁移后删除的文件（补充2）

```
backend/services/kuaimai/formatters/  (Phase 5B 的 42 个 formatter，配置迁移到 tool_formatter_configs 后删除)
  ├── product.py   → 6 个 formatter 的 _LABELS/_TRANSFORMS 写入 DB
  ├── trade.py     → 6 个 formatter 的配置写入 DB
  ├── basic.py     → 5 个 formatter 的配置写入 DB
  ├── warehouse.py → 10 个 formatter 的配置写入 DB
  ├── purchase.py  → 7 个 formatter 的配置写入 DB
  ├── aftersales.py → 6 个 formatter 的配置写入 DB
  └── qimen.py     → 2 个 formatter 的配置写入 DB
  注意: common.py 中的 format_item_with_labels() 迁移到 response_formatter.py 保留
```

### 保留的文件

```
backend/services/kuaimai/client.py  (HTTP + 签名认证, UniversalDispatcher 复用)
backend/services/kuaimai/param_mapper.py  (别名解析/同义兜底/日期补全, 迁移到 param_type_engine 后删除)
```

---

## 九、分阶段实施

### Phase 1: DocParser + 基础框架 + 参数类型系统
- DB 迁移（tool_actions 含 semantic_type 字段 + tool_formatter_configs 表）
- 数据模型（含 ParsedResponseField）
- DocParser（正则解析 + AI 增强，含任务D语义类型推断 + 任务E响应格式化配置）
- ParamTypeEngine（参数类型运行时引擎，从 param_mapper/param_guardrails 迁移）
- ERP 数据迁移脚本（含 formatter 配置迁移）
- tool_builder + universal_dispatcher（集成 ParamTypeEngine）
- 集成 agent_tools + tool_executor
- 测试：解析 kuaimai_api_summary.md → 验证 actions 数量和质量 + 语义类型准确性

### Phase 2: RuleExtractor + PromptBuilder
- 规则正则提取
- 规则 AI 推断
- prompt_builder 动态生成路由提示词
- 替代 ERP_ROUTING_PROMPT
- 测试：对比生成的规则与现有手写规则

### Phase 3: DataProfiler + ResponseFormatter
- 采样 + 分析模块（含模块C2 Transform 自动检测）
- ResponseFormatter 运行时引擎（从 DB 加载配置，复用 format_item_with_labels）
- AI 汇总（含枚举值中文标签生成）
- 画像注入到 agent_context
- formatter 配置写入 tool_formatter_configs
- 测试：绑定 ERP 后自动生成画像 + 格式化输出与 Phase 5B 一致

### Phase 4: FeedbackLoop
- 调用日志记录
- 失败分析
- 自动修复
- 测试：模拟失败场景验证规则自更新

### Phase 5: Formatter 迁移 + 硬编码删除
- 运行迁移脚本：Phase 5B 的 42 个 formatter 配置 → tool_formatter_configs
- 对比验证：DB 驱动输出 vs 硬编码输出，逐 action diff
- 删除 formatters/ 目录（保留 format_item_with_labels 到 response_formatter.py）
- 端到端验证：用户对话查询 → 格式化输出与迁移前一致

---

## 十、验证方式

1. **DocParser**：解析 `kuaimai_api_summary.md` → 对比输出的 actions 数量 vs 现有 registry 中的 actions 数量（应 ≥ 现有）
2. **DocParser 任务D**：语义类型推断准确性 → 对比 vs 现有 param_mapper/param_guardrails 中的硬编码规则（覆盖率应 ≥ 90%）
3. **DocParser 任务E**：自动生成的 labels/transforms → 对比 vs Phase 5B 手写的 `_LABELS`/`_TRANSFORMS`（字段覆盖率 ≥ 85%，transform 类型准确率 ≥ 90%）
4. **ParamTypeEngine**：用 ERP 现有测试用例验证 → 同义兜底/编码宽泛查询/日期补全/分页标准化 行为与迁移前一致
5. **RuleExtractor**：生成的规则 vs 现有 `ERP_ROUTING_PROMPT` 145 行 → 覆盖率应 ≥ 80%
6. **DataProfiler + ResponseFormatter**：
   - 用真实 ERP 账号采样 → 画像内容合理性人工验证
   - Transform 自动检测准确率 → 对比 vs Phase 5B 手写 transforms（时间戳 100%、金额 95%、枚举 90%）
   - 格式化输出 diff → DB 驱动 vs Phase 5B 硬编码，逐 action 对比（差异率 < 5%）
7. **FeedbackLoop**：故意传错参数 → 验证规则自动生成
8. **端到端**：用户正常对话查询 ERP → 功能与迁移前一致
9. **客户自注册**：新 API 选参数类型后 → 验证运行时智能自动生效（同义兜底/编码查询等）
10. **客户自注册（格式化）**：新 API 注册后 → 自动生成 formatter 配置 → 格式化输出可读（非 raw JSON）

```bash
source backend/venv/bin/activate && python -m pytest backend/tests/test_tool_registry/ -q --tb=short
```

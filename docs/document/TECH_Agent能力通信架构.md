# TECH_Agent能力通信架构

> 版本：v1.4 | 日期：2026-04-19 | 状态：方案确认
> v1.1 补充代码审核发现的 3 个必修问题
> v1.2 整合评审结论（种子计划 19 条 + 能力边界标注 + _fetch_knowledge 保持 list 返回）
> v1.3 manifest 包含全部结构化数据（能力+边界+示例+返回），build_tool_description 纯格式化
> v1.4 字段动态选择：EXPORT_COLUMNS 分类注入 manifest + plan_builder fields 参数提取 + 单一职责分工（主 Agent 看分类，erp_agent 选字段名）

## 一、问题来源

Tool Digest（跨轮上下文补全）上线后测试发现更深层问题：**主 Agent 对子 Agent 的能力认知严重不足**。

实际案例：用户追问"刷单集中在哪些商品"，主 Agent 反复调 erp_agent 13 次，每次都拿到 488 字节全局汇总——因为它不知道 erp_agent 支持 `group_by`（按维度分组）、`detail` 模式（明细列表）等能力。erp_agent 有 13 项能力，工具描述只提到了 2 项。

### 1.1 三个断裂点

| 断裂点 | 根因 | 影响 |
|-------|------|------|
| **能力描述断裂** | `chat_tools.py` 手写 14 行描述，与 `plan_builder.py` 的 10+ 参数定义分离维护 | 主 Agent 不知道 erp_agent 能做什么 |
| **经验积累断裂** | `experience_recorder` 写入 `knowledge_nodes`，但 `_fetch_knowledge` 不做定向召回 | 存了经验没人读 |
| **能力源头不全** | `plan_builder.build_extract_prompt` 自己也只声明了 2/6 个 group_by 维度 | 即使描述补全也提取不出新维度 |

### 1.2 能力差距全表

**erp_agent 实际能力 vs 各层认知：**

| 能力 | UnifiedQueryEngine 支持 | plan_builder 提取 | 主 Agent 描述 |
|------|:-:|:-:|:-:|
| 4 域（trade/warehouse/purchase/aftersale） | ✅ | ✅ | ✅ |
| 6 doc_type | ✅ | ✅ | ❌ |
| 3 模式（summary/detail/export） | ✅ | ✅ | ❌ |
| group_by=shop/platform | ✅ | ✅ | ❌ |
| **group_by=product/supplier/warehouse/status** | **✅** | **❌** | **❌** |
| 平台过滤（7 平台） | ✅ | ✅ | ❌ |
| product_code/order_no 过滤 | ✅ | ✅ | ❌ |
| include_invalid 异常控制 | ✅ | ✅ | ❌ |
| time_col=pay_time/doc_created_at | ✅ | ✅ | ❌ |
| **time_col=consign_time** | **✅** | **❌** | **❌** |
| >200行自动导出 staging 文件 | ✅ | — | ❌ |
| 返回格式自动适配 | ✅ | — | ❌ |

**粗体 = 两层都断裂（源头 plan_builder 自己也不知道）。**

### 1.3 代码审核发现的隐藏 Bug

**Bug 1（致命）：group_by 类型不兼容——现有功能也是坏的**

验证完整调用链后发现：`plan_builder._sanitize_params()` 透传 `group_by="shop"`（字符串），但 `UnifiedQueryEngine.execute()` 期望 `list[str]`。

```
LLM 返回: {"group_by": "shop"}
  → _sanitize_params: group_by="shop" (字符串原样透传)
  → department_agent: kwargs.get("group_by") = "shop"
  → execute(group_by="shop"): for g in "shop" → 逐字符迭代 ["s","h","o","p"]
  → 全部不在 GROUP_BY_MAP → group_by 被静默清空为 None
  → 查询变成无分组的全局汇总
```

**证据**：
- `plan_builder.py:119-120`：`clean["group_by"] = params["group_by"]`（不做类型转换）
- `erp_unified_query.py:80`：参数声明 `group_by: list[str] | None = None`
- `erp_unified_query.py:105`：`valid_groups = [g for g in group_by if g in GROUP_BY_MAP]`（逐字符迭代）
- `test_plan_builder.py:88`：`assert result["group_by"] == "shop"`（测试只验证 sanitize 输出，没验证下游兼容性）
- `test_erp_data_integrity.py:226`：直接传 `group_by=["shop"]`（list），绕过了 plan_builder 链路

**修复位置**：`plan_builder._sanitize_params()`，将标量字符串转为列表。

**Bug 2：经验记录内容太泛——无法作为有效 few-shot**

`ExperienceRecorder.record()` 成功时 `detail` 参数固定为 `"单域查询"`：

```python
# erp_agent.py:199-201
asyncio.create_task(self._experience.record(
    "routing", query, [domain],
    "单域查询", confidence=0.6,  # ← detail 永远是这 4 个字
))
```

实际存储内容：
```
查询：各平台退货率
路径：warehouse
单域查询          ← 没有记录用了什么参数（group_by? mode? platform?）
耗时：1.2s
```

作为 few-shot 几乎无用——LLM 看到"单域查询"不知道具体怎么查的。

**修复**：detail 应包含关键参数，如 `"domain=trade, mode=summary, group_by=platform"`。

**Bug 3：经验注入未区分类型——与通用知识混在一起**

当前 `_fetch_knowledge` 注入格式：
```
你已掌握的经验知识：
- 查询路由：各平台退货率：查询：各平台退货率\n路径：warehouse\n单域查询\n耗时：1.2s
- 模型知识：gemini-3-pro 适合复杂推理...
```

经验案例和模型/工具种子知识混在一起，LLM 难以区分"这是过去成功的查询方式"还是"这是通用知识"。

**修复**：经验类结果加 `_source` tag，注入时按 tag 分离为独立 system message。

### 1.4 能力边界

以下能力底层 UnifiedQueryEngine 支持，但 plan_builder 不提取参数：

| 能力 | 底层支持 | plan_builder 提取 | 本次处理 |
|------|:---:|:---:|------|
| **fields 指定返回字段** | ✅ | ❌ | **✅ 本次修复**（阻塞性——通信层再完美，数据层拿不到字段等于白调） |
| warehouse_name 过滤 | ✅ filter DSL | ❌ | 标注边界，后续迭代 |
| supplier_name 过滤 | ✅ filter DSL | ❌ | 标注边界，后续迭代 |
| 多维度 group_by | ✅ 参数是 list | ❌（RPC 只取 group_by[0]） | 标注边界，用 group_by + code_execute 二次分组替代 |
| sort_by / limit | ✅ | ❌ | 标注边界，用 group_by + code_execute 排序替代 |

**fields 修复原因**：底层 `UnifiedQueryEngine.execute()` 第 321 行已支持 `fields` 参数覆盖默认字段，`department_agent` 第 470 行已透传 `kwargs.get("fields")`。只差 plan_builder 提取 + 主 Agent 知道有哪些字段分类。

**字段规模**：`COLUMN_WHITELIST` 36 个查询字段，`EXPORT_COLUMN_NAMES` 55 个导出字段，`DEFAULT_DETAIL_FIELDS` 每个 doc_type 只返回 8-10 个。差距 = 45 个字段用户问到就断裂。

**全量返回不可行**：100 行 × 55 字段 ≈ 28000 token，接近 context_max_tokens=32000 上限。大厂做法（LangChain SQL Agent）：**"Never query for all columns, only ask for relevant columns given the question"**。

**我们的方案**：两层分工，符合子 Agent 单一职责——
- 主 Agent 看**字段分类**（10 个类别，~50 token）→ 判断任务可行性
- erp_agent 内部 LLM 看**字段清单**（55 个字段名+中文名）→ 根据 query 动态选字段

**现有基础设施**：`EXPORT_COLUMNS`（`erp_unified_schema.py`）已按 10 个分类组织 55 个字段，每个字段有中文名。直接 import 即可。

---

## 二、行业调研

### 2.1 大厂工具能力注册方案

| 方案 | 代表 | 核心思路 | 适用场景 |
|------|------|---------|---------|
| **自描述工具** | Microsoft Semantic Kernel | 装饰器/反射自动生成描述，改代码=改描述 | 函数式工具 |
| **Agent Card** | Google A2A 协议 | JSON 名片声明 skills[]，连接时读取 | Agent-to-Agent |
| **Handoff** | OpenAI Agents SDK | 子 Agent = tool，description 写在定义时 | 多 Agent 编排 |
| **MCP** | Anthropic | Server 握手时声明 tools/resources/prompts | 跨进程工具 |
| **语义搜索** | LangGraph BigTool | Agent 有 meta-tool 搜索工具注册表 | 100+ 工具 |

**共同结论**：三家大厂（Google/OpenAI/Anthropic）都是**静态声明**，运行时不做动态发现。只有 LangGraph 做了搜索，且明确是给 100+ 工具场景用的。

**我们的选择**：采用 Google A2A Agent Card 的**结构化声明思路**，但不引入协议框架。当前只有 1 个子 Agent，静态声明足够。

### 2.2 大厂工具描述最佳实践

调研了 OpenAI o3/o4-mini Function Calling Guide + Anthropic 官方文档，工具描述的标准格式为 **5 段式**：

```
① 一句话说做什么（功能定义）
② 什么时候用 + 什么时候不用（决策边界）
③ 能力/模式/参数说明（完整能力清单）
④ 返回什么（输出格式）
⑤ few-shot 示例（query → 行为映射）
```

**关键发现**：
- OpenAI 实测：加 few-shot 示例比纯文字描述**准确率高 6%**
- Anthropic API 层直接支持 `input_examples` 字段
- 核心原则："Describe your tool like you would to a new hire on your team"
- 复杂工具的关键是**示例**，不是更长的描述文字

### 2.3 大厂怎么解决多工具编排

**OpenAI**：不写编排模式。工具描述写完整，LLM 自己推理编排。
> "A single agent can handle many tasks by incrementally adding tools"

**Claude Code**：同样不预设编排模式。
> "Tool selection is model-driven: Claude sees the full tool definitions and produces the next message"

**结论**：编排能力来自**完整的工具描述**，不来自预设的编排模式。主 Agent 不会编排"各平台退货率"不是因为推理能力不够，是因为不知道 erp_agent 支持 `group_by=platform`。

### 2.4 大厂怎么做案例注入

调研 LangChain Few-Shot Tool Calling 对比实验 + Anthropic input_examples 机制：

**两层共存，每次请求都注入**：
- 静态示例写在工具定义里，每次请求 100% 带上
- 动态案例按语义相似度搜索，有结果就注入，没有就跳过
- LangChain 实测：**动态选 3 个语义相似示例 > 固定 13 个静态示例**（相关性比数量重要）
- 冷启动期只有静态示例，随使用积累动态案例越来越多

**种子数据解决冷启动**：没有种子 = 死循环（没成功案例→不会正确调用→记录不到成功→永远没案例）。

---

## 三、架构设计

### 3.1 核心思路：两层机制

```
第一层（静态）: 工具描述按大厂 5 段式格式，从 plan_builder 定义自动生成
                → 解决"主 Agent 不知道能做什么"
                → 解决"描述与实现漂移"

第二层（动态案例）: 用户查询进来时，语义搜索历史成功案例注入为 few-shot 示例
                → 解决"新场景没有编排模式"
                → 越用越准，不用手写模式
                → 种子数据保证冷启动覆盖
```

### 3.2 数据流

```
plan_builder.py + erp_unified_schema.py（Source of Truth）
  ├── VALID_DOMAINS, VALID_MODES, GROUP_BY_MAP, PLATFORM_NORMALIZE...
  ├── EXPORT_COLUMNS（10 分类 × 55 字段 + 中文名）  ← 字段分类数据源
  └── get_capability_manifest()  ←── 新增，导出完整结构化清单
           │                         （能力 + 决策边界 + 返回 + 示例 + 字段分类 + 自动行为）
           │
           │ 调用
           ↓
erp_agent.py
  └── build_tool_description() → 纯格式化：manifest dict → 5 段式文本
           │                      （不含任何硬编码内容，只做模板渲染）
           │
           │ 调用
           ↓
chat_tools.py 自动生成 erp_agent 的 description
           │
           │ 注入 LLM
           ↓
主 Agent 看到完整能力描述 + 静态 5 示例
           │
           │ 成功调用
           ↓
experience_recorder 记录成功路径（含关键参数 detail）
           │
           │ 写入
           ↓
knowledge_nodes 表（category=experience, node_type=routing_pattern）
  ↑                    │
  │ 种子数据冷启动       │ 下次查询时
  │ seed_knowledge.json  ↓
  │                _fetch_knowledge → search_relevant(category=experience) → 语义匹配
  │                    │
  │                    │ 注入为独立 system message
  │                    ↓
  └──────────── 主 Agent 看到"历史成功案例：上次用了 group_by=shop"
```

**闭环**：种子冷启动 → 描述从源头自动生成 → 主 Agent 正确调用 → 成功经验自动记录 → 下次类似场景自动召回 → 越用越准。

### 3.3 静态层：get_capability_manifest 完整结构 + build_tool_description 纯格式化

#### 3.3.1 get_capability_manifest() 返回的完整结构化数据

所有内容结构化，`build_tool_description()` 不含任何硬编码：

```python
def get_capability_manifest() -> dict:
    """导出 erp_agent 完整能力清单（唯一 Source of Truth）"""
    from services.kuaimai.erp_unified_schema import (
        GROUP_BY_MAP, VALID_TIME_COLS, PLATFORM_NORMALIZE,
        EXPORT_COLUMNS,
    )
    # 去重提取 group_by 维度
    group_by_dims = sorted({v for v in GROUP_BY_MAP.values()})
    # 去重提取平台中文名
    platform_names = sorted({
        k for k in PLATFORM_NORMALIZE if not k.isascii()
    })
    # 从 EXPORT_COLUMNS 生成字段分类摘要（只取中文名，不暴露英文字段名）
    field_categories = {
        category: [cn_name for _, cn_name in fields]
        for category, fields in EXPORT_COLUMNS.items()
    }

    return {
        # ── 能力数据（从常量自动生成，改常量=改描述）──
        "domains": sorted(VALID_DOMAINS),
        "modes": sorted(VALID_MODES),
        "doc_types": sorted(VALID_DOC_TYPES),
        "group_by": group_by_dims,
        "filters": ["platform", "product_code", "order_no", "include_invalid"],
        "time_cols": sorted(VALID_TIME_COLS),
        "platforms": platform_names,

        # ── 字段分类（主 Agent 看分类判断可行性，erp_agent 内部选具体字段）──
        "field_categories": field_categories,

        # ── 决策边界 ──
        "summary": "ERP 数据查询专员，查询订单/库存/采购/售后等数据，口语化表达和错别字自动识别",
        "use_when": [
            "用户问任何涉及订单/库存/采购/售后/发货/物流/商品/销量的问题",
            "含操作性词汇（对账/核对/处理）需要先查数据",
        ],
        "dont_use_when": [
            {"场景": "写操作（创建/修改/取消）", "替代": "erp_execute"},
            {"场景": "非 ERP 数据（天气/新闻）", "替代": "web_search"},
            {"场景": "业务规则/操作流程", "替代": "search_knowledge"},
        ],

        # ── 返回说明 ──
        "returns": [
            "summary 模式：统计数字（总量/金额/分组明细）",
            "detail 模式：数据表格，>200行自动生成文件链接",
            "每次只查一个业务域，跨域数据并行调用多次",
            "query 中提到具体信息（如'备注''地址''快递单号'）会自动返回对应字段",
        ],

        # ── 静态 few-shot 示例 ──
        "examples": [
            {"query": "昨天淘宝退货按店铺统计", "effect": "summary + platform=taobao + group_by=shop"},
            {"query": "本周订单明细列表", "effect": "detail 模式"},
            {"query": "编码 HZ001 的库存", "effect": "product_code 过滤"},
            {"query": "上月采购到货按供应商统计", "effect": "summary + group_by=supplier"},
            {"query": "包含刷单的订单有多少", "effect": "include_invalid=true"},
        ],

        # ── 自动行为 ──
        "auto_behaviors": [
            ">200行自动导出 staging 文件",
            "返回格式自动适配（文本/表格/文件链接）",
            "降级链：AI提取 → 关键词匹配 → abort",
        ],
    }
```

#### 3.3.2 build_tool_description() 纯格式化

```python
@staticmethod
def build_tool_description() -> str:
    """从 manifest 格式化为 5 段式描述文本（纯模板渲染，不含硬编码）"""
    from services.agent.plan_builder import get_capability_manifest
    m = get_capability_manifest()

    # ① 功能定义
    lines = [m["summary"]]

    # ② 决策边界
    lines.append("\n使用场景：" + "；".join(m["use_when"]))
    dont = " / ".join(f"{d['场景']}→{d['替代']}" for d in m["dont_use_when"])
    lines.append(f"不要用于：{dont}")

    # ③ 能力清单
    lines.append(f"\n能力：")
    lines.append(f"- 输出模式：{' / '.join(m['modes'])}（>200行自动导出文件）")
    lines.append(f"- 分组统计：按{'/'.join(m['group_by'])}统计")
    lines.append(f"- 过滤：自动识别{'、'.join(m['platforms'])}、商品编码、订单号")
    lines.append(f"- 时间列：{' / '.join(m['time_cols'])}（默认 doc_created_at）")
    lines.append(f"- 异常数据：默认排除刷单，query 中写'包含刷单'则包含")

    # ③+ 可查询信息分类（主 Agent 判断任务可行性）
    categories = m.get("field_categories", {})
    if categories:
        cat_summary = "/".join(categories.keys())
        lines.append(f"- 可查询信息：{cat_summary}")
        lines.append(f"  （query 中提到具体信息如'备注''地址''快递单号'会自动返回对应字段）")

    # ④ 返回说明
    lines.append(f"\n返回：")
    for r in m["returns"]:
        lines.append(f"- {r}")

    # ⑤ few-shot 示例
    lines.append(f"\nquery 示例：")
    for ex in m["examples"]:
        lines.append(f"· \"{ex['query']}\" → {ex['effect']}")

    return "\n".join(lines)
```

**职责分离**：改内容 → 改 `get_capability_manifest()`；改格式 → 改 `build_tool_description()`。两者独立。

**数据来源**（全部 import 引用，不手写）：
- `VALID_DOMAINS` ← `plan_builder.py`
- `VALID_MODES` ← `plan_builder.py`
- `VALID_DOC_TYPES` ← `plan_builder.py`
- `GROUP_BY_MAP` 去重 ← `erp_unified_schema.py`
- `VALID_TIME_COLS` ← `erp_unified_schema.py`
- `PLATFORM_NORMALIZE` 去重 ← `erp_unified_schema.py`
- `EXPORT_COLUMNS` 分类名 ← `erp_unified_schema.py`（新增：字段分类）

### 3.4 动态案例层：种子 + 经验召回

#### 3.4.1 种子数据（冷启动保障）

19 个种子案例，覆盖 27 个能力点。存入 `data/seed_knowledge.json`，category=`experience`，node_type=`routing_pattern`，source=`seed`，confidence=`0.9`。

**A. 单次调用能力覆盖（11 个种子）**

| # | 种子查询 | 结果摘要 | 覆盖能力点 |
|---|---------|---------|-----------|
| 1 | 昨天淘宝订单按店铺统计 | domain=trade, doc_type=order, mode=summary, platform=taobao, group_by=shop | doc_type=order + mode=summary + platform filter + group_by=shop |
| 2 | 本周退货明细列表 | domain=aftersale, doc_type=aftersale, mode=detail | doc_type=aftersale + mode=detail |
| 3 | 上月采购单按供应商统计 | domain=purchase, doc_type=purchase, mode=summary, group_by=supplier | doc_type=purchase + group_by=supplier |
| 4 | 采退明细按状态统计 | domain=purchase, doc_type=purchase_return, mode=summary, group_by=status | doc_type=purchase_return + group_by=status |
| 5 | 本月入库单按仓库统计 | domain=warehouse, doc_type=receipt, mode=summary, group_by=warehouse | doc_type=receipt + group_by=warehouse |
| 6 | 上架单明细 | domain=warehouse, doc_type=shelf, mode=detail | doc_type=shelf |
| 7 | 刷单集中在哪些商品 | domain=trade, mode=summary, group_by=product, include_invalid=true | group_by=product + include_invalid |
| 8 | 编码 HZ001 的库存 | domain=warehouse, product_code=HZ001 | product_code filter |
| 9 | 订单号 T12345 的详情 | domain=trade, order_no=T12345 | order_no filter |
| 10 | 昨天按平台统计发货量 | domain=trade, mode=summary, group_by=platform, time_col=consign_time | group_by=platform + time_col=consign_time |
| 11 | 本月付款订单汇总 | domain=trade, mode=summary, time_col=pay_time | time_col=pay_time |

**B. 多工具编排覆盖（8 个种子）**

| # | 种子查询 | 编排方式 | 覆盖能力点 |
|---|---------|---------|-----------|
| 12 | 各平台退货率 | 并行 erp_agent(订单按平台统计) + erp_agent(退货按平台统计) → code_execute(退货数/订单数) | 并行 erp_agent + erp_agent→code_execute |
| 13 | 缺货商品的采购进度 | 并行 erp_agent(缺货商品明细) + erp_agent(采购在途明细) → code_execute(关联) | 跨域并行 |
| 14 | 某订单的物流轨迹 | erp_agent(查订单拿 system_id) → erp_agent(用 system_id 查物流) | 链式 erp_agent |
| 15 | 各店铺本月销售额排名 | erp_agent(订单按店铺统计) → code_execute(排序)。不要逐店铺查，用 group_by 一次搞定 | group_by 避免 N+1 |
| 16 | 淘宝和拼多多退货数对比 | 并行 erp_agent(淘宝退货统计, platform=taobao) + erp_agent(拼多多退货统计, platform=pdd) → code_execute(对比) | 指定平台并行对比 |
| 17 | 各平台各店铺销售额 | erp_agent(订单按平台统计, group_by=platform) → code_execute(从明细数据按店铺二次分组) | 单维 group_by + code_execute 二次分组 |
| 18 | 采购单备注里写了什么 | erp_agent(采购单明细, fields=["remark","doc_code","supplier_name"]) | fields 参数 + 非默认字段 |
| 19 | 待发货订单超时+采购到货+库存 | 并行 erp_agent(待发货订单) + erp_agent(采购在途) + erp_agent(库存) → code_execute(关联计算超时) | 3 域并行 + 复杂计算 |

**C. 覆盖验证矩阵**

| 能力点 | 被哪个种子覆盖 |
|-------|-------------|
| doc_type=order | #1 |
| doc_type=aftersale | #2 |
| doc_type=purchase | #3 |
| doc_type=purchase_return | #4 |
| doc_type=receipt | #5 |
| doc_type=shelf | #6 |
| mode=summary | #1 #3 #4 #5 #7 #10 #11 |
| mode=detail | #2 #6 |
| group_by=shop | #1 |
| group_by=platform | #10 |
| group_by=product | #7 |
| group_by=supplier | #3 |
| group_by=warehouse | #5 |
| group_by=status | #4 |
| platform filter | #1 |
| product_code filter | #8 |
| order_no filter | #9 |
| include_invalid | #7 |
| time_col=pay_time | #11 |
| time_col=consign_time | #10 |
| 并行 erp_agent | #12 #13 #16 #19 |
| erp_agent + code_execute | #12 #15 #17 |
| 链式 erp_agent | #14 |
| group_by 避免 N+1 | #15 |
| 指定平台并行对比 | #16 |
| 单维 group_by + code_execute 二次分组 | #17 |
| fields 动态选字段（非默认字段） | #18 |

**27/27 全覆盖。**

#### 3.4.2 经验召回注入

**写入端**（已有 + Phase 1 改进 detail）：
- `ExperienceRecorder.record()` → `knowledge_nodes` 表
- category=`experience`，node_type=`routing_pattern`/`failure_pattern`
- content 格式：`查询：{query}\n路径：{tools}\n{detail}\n耗时：{elapsed}`
- **Phase 1 改进**：detail 从 `"单域查询"` → `"domain=trade, mode=summary, group_by=platform"`

**读取端**（Phase 3 新增）：
- `_fetch_knowledge()` 增加一路并行召回，返回类型保持 `list | None`
- experience 结果加 `_source="experience"` tag
- 注入时按 tag 分离为独立 system message

**匹配机制**：
- `search_relevant` 已支持 pgvector 语义搜索（1024维 embedding）
- 排序：`similarity * 0.7 + confidence * 0.3`
- 种子 confidence=0.9（高于普通经验的 0.6），冷启动时优先被召回
- 命中后自动 `confidence += boost`，越被召回置信度越高
- 不完全匹配也有价值——近似案例给 LLM 方向，LLM 自己推导补齐

---

## 四、现有基础设施复用评估

| 组件 | 文件 | 复用状态 | 说明 |
|------|------|---------|------|
| `ExperienceRecorder` | `experience_recorder.py` | ✅ **直接复用** | 多 Agent 设计（`writer` 参数），无需改动 |
| `search_relevant()` | `knowledge_service.py` | ✅ **直接复用** | 已支持 `category`/`node_type`/`min_confidence` 过滤 |
| `format_knowledge_node()` | `knowledge_config.py` | ✅ **直接复用** | 返回 flat dict，格式干净 |
| `load_seed_knowledge()` | `knowledge_service.py` | ✅ **直接复用** | 读 `seed_knowledge.json`，启动时自动导入 |
| `_fetch_knowledge()` | `chat_context_mixin.py` | ⚠️ **需扩展** | 加一路并行召回 experience 类型 |
| `TOOL_SYSTEM_PROMPT` | `chat_tools.py` | ✅ **保留不动** | 跨工具编排规则，与能力描述分离 |
| `get_tool_system_prompt()` | `chat_tools.py` | ✅ **保留不动** | 注入模式可复用 |
| `_build_common_tools()` | `chat_tools.py` | ⚠️ **需改造** | erp_agent 的 dict 改为从 build_tool_description 自动生成 |
| `VALID_DOMAINS` | `plan_builder.py` | ⚠️ **需公开** | 当前为模块私有常量，需要导出供描述生成引用 |
| `_VALID_MODES` | `plan_builder.py` | ⚠️ **需公开** | 同上 |
| `GROUP_BY_MAP` | `erp_unified_schema.py` | ✅ **直接复用** | 已有完整的 6 维度映射 |
| `VALID_TIME_COLS` | `erp_unified_schema.py` | ✅ **直接复用** | 3 个时间列 |
| `PLATFORM_NORMALIZE` | `erp_unified_schema.py` | ✅ **直接复用** | 完整平台映射表 |
| `EXPORT_COLUMNS` | `erp_unified_schema.py` | ✅ **直接复用** | 10 分类 × 55 字段 + 中文名，字段分类数据源 |
| `DepartmentAgent(ABC)` | `department_agent.py` | ✅ **设计参考** | 成熟的抽象基类模式 |

**复用率：15 个组件中 10 个直接复用，4 个需小幅改造，1 个作设计参考。无需新建基础设施。**

---

## 五、实施方案

### Phase 1：修源头——plan_builder 补全 + Bug 修复 + 种子数据

**目标**：erp_agent 内部 LLM 能提取完整参数，类型链路兼容，经验记录有效，冷启动有种子。

**修改文件**：
- `backend/services/agent/plan_builder.py`
- `backend/services/agent/erp_agent.py`（经验记录 detail 改进）
- `backend/data/seed_knowledge.json`（追加 19 条 experience 种子）

**改动**：
1. `build_extract_prompt()` 的参数定义补全：
   - `group_by` 从 `shop/platform` → `shop/platform/product/supplier/warehouse/status`
   - `time_col` 补充 `consign_time`（发货时间）说明
   - 补充 group_by few-shot 示例（示例3：按商品分组统计退货）

2. **修复 group_by 类型不兼容 Bug**（§1.3 Bug 1）：
   - `_sanitize_params()` 中将 `group_by` 标量字符串转为列表：
     ```python
     if params.get("group_by"):
         gb = params["group_by"]
         clean["group_by"] = [gb] if isinstance(gb, str) else gb
     ```

3. 导出常量供描述生成引用：
   - 将 `_VALID_MODES`、`_VALID_DOC_TYPES` 改为公开变量（去掉下划线前缀）
   - 新增 `VALID_GROUP_BY` 从 `GROUP_BY_MAP` 提取去重后的维度列表

4. 新增 `get_capability_manifest() -> dict`：
   - 从现有常量 + `erp_unified_schema` 的定义自动组装
   - 返回结构化能力清单供 `build_tool_description()` 消费

5. **改进经验记录 detail**（§1.3 Bug 2）：
   - `erp_agent._build_result()` 中 routing 记录的 detail 改为包含关键参数：
     ```python
     detail = f"domain={domain}, mode={params.get('mode','summary')}"
     if params.get("group_by"):
         detail += f", group_by={params['group_by']}"
     if params.get("platform"):
         detail += f", platform={params['platform']}"
     ```

6. **plan_builder 新增 fields 参数提取**（§1.4 fields 修复）：
   - `build_extract_prompt()` 新增 fields 参数定义，按分类列出可选字段：
     ```
     - fields: 需要返回的特定字段（可选，用户明确提到特定信息时提取）
       可选字段分类：
       备注类: remark(备注), buyer_message(买家留言), sys_memo(系统备注)
       物流类: express_no(快递单号), express_company(快递公司)
       买家类: buyer_nick(买家昵称), receiver_name(收件人), receiver_address(地址)
       金额类: cost(成本), gross_profit(毛利), discount_fee(优惠), post_fee(运费)
       售后类: text_reason(退货原因), refund_warehouse_name(退货仓库)
       状态类: is_cancel(是否取消), is_refund(是否退款), is_exception(是否异常)
       注意：不提则用默认字段，不要主动添加用户未提到的字段
     ```
   - `_sanitize_params()` 新增 fields 白名单校验：
     ```python
     if params.get("fields"):
         fields = params["fields"]
         if isinstance(fields, str):
             fields = [fields]
         valid = COLUMN_WHITELIST.keys() | EXPORT_COLUMN_NAMES
         clean["fields"] = [f for f in fields if f in valid]
     ```

7. **追加 19 条种子数据**（§3.4.1）：
   - 格式与现有 seed_knowledge.json 一致
   - category=`experience`，node_type=`routing_pattern`，source=`seed`，confidence=`0.9`
   - 覆盖全部 27 个能力点（含 fields 参数）

**测试**：
- `build_extract_prompt` 输出包含 6 个 group_by 维度 + consign_time + group_by 示例 + fields 分类
- `_sanitize_params({"group_by": "shop"})` 返回 `{"group_by": ["shop"]}`
- `_sanitize_params({"group_by": ["shop", "platform"]})` 保持列表不变
- `_sanitize_params({"fields": "remark"})` 返回 `{"fields": ["remark"]}`
- `_sanitize_params({"fields": ["remark", "invalid_col"]})` 过滤掉非法字段
- `get_capability_manifest()` 返回结构包含 domains/modes/group_by/time_cols/platforms/field_categories
- `load_seed_knowledge()` 成功导入 experience 类型种子

### Phase 2：静态层——manifest 结构化 + build_tool_description 纯格式化

**目标**：主 Agent 看到的 erp_agent 工具描述从 manifest 自动格式化，内容与格式分离。

**修改文件**：
- `backend/services/agent/erp_agent.py`（新增 `build_tool_description()` 静态方法）
- `backend/config/chat_tools.py`（erp_agent 的 dict 改为从 build_tool_description 生成）

**改动**：
1. `get_capability_manifest()` 扩充为完整结构化数据（§3.3.1）：
   - 能力数据：从常量自动生成（domains/modes/group_by/time_cols/platforms）
   - 决策边界：use_when / dont_use_when（结构化 list/dict）
   - 返回说明：returns（结构化 list）
   - 静态示例：examples（结构化 list[dict]）
   - 自动行为：auto_behaviors（结构化 list）
   - **所有内容结构化，未来可被 API/测试/其他 Agent 直接消费**

2. `ERPAgent` 新增 `build_tool_description() -> str` 静态方法（§3.3.2）：
   - 纯模板渲染：从 manifest 格式化为 5 段式文本
   - **不含任何硬编码内容**——改内容改 manifest，改格式改 formatter
   - 此方法生成的文本替代 `chat_tools.py` 中手写的 14 行描述

3. `chat_tools.py` 的 `_build_common_tools()` 中 erp_agent 部分改造：
   - `description` 字段改为调用 `ERPAgent.build_tool_description()`
   - `query` 参数的 description 保留现有内容（补全指代词等使用说明仍有效）

**测试**：
- `get_capability_manifest()` 返回 dict 包含所有 key（domains/modes/group_by/use_when/examples 等）
- `build_tool_description()` 输出包含所有能力维度（6 group_by / 3 time_col / 7 平台 / 3 模式）
- 生成的描述 token 数 < 400（`len(description) / 2.5 < 400`）
- `get_chat_tools()` 返回的 erp_agent schema 包含完整描述
- manifest 中 platforms 从 PLATFORM_NORMALIZE 自动生成（加新平台无需改 manifest 代码）
- manifest 中 group_by 从 GROUP_BY_MAP 自动生成（加新维度无需改 manifest 代码）
- manifest 中 field_categories 从 EXPORT_COLUMNS 自动生成（加新字段无需改 manifest 代码）
- `build_tool_description()` 输出包含"可查询信息：单据基础/时间/商品/..."分类行

### Phase 3：动态案例层——经验定向召回

**目标**：历史成功案例自动注入为 few-shot 示例，与通用知识分离，越用越准。

**修改文件**：`backend/services/handlers/chat_context_mixin.py`

**改动**：

1. `_fetch_knowledge()` 改为两路并行召回，返回类型保持 `list | None`：
   ```python
   async def _fetch_knowledge(self, query: str) -> Optional[list]:
       if not query:
           return None
       try:
           from services.knowledge_service import search_relevant
           general, experience = await asyncio.gather(
               search_relevant(query=query, limit=3, org_id=self.org_id),
               search_relevant(
                   query=query,
                   limit=2,
                   category="experience",
                   node_type="routing_pattern",
                   min_confidence=0.6,
                   org_id=self.org_id,
               ),
               return_exceptions=True,
           )
           g = general if not isinstance(general, BaseException) else []
           e = experience if not isinstance(experience, BaseException) else []
           # experience 结果加 tag，注入时按 tag 分离
           for item in (e or []):
               item["_source"] = "experience"
           result = (g or []) + (e or [])
           return result if result else None
       except Exception as ex:
           logger.debug(f"Knowledge fetch skipped | error={ex}")
           return None
   ```

2. `_build_llm_messages()` 中注入逻辑按 `_source` tag 分离（§1.3 Bug 3）：
   ```python
   if knowledge_items and not self._should_skip_knowledge(text_content):
       # 分离通用知识和经验案例
       general = [k for k in knowledge_items if k.get("_source") != "experience"]
       experience = [k for k in knowledge_items if k.get("_source") == "experience"]

       if general:
           knowledge_text = "\n".join(f"- {k['title']}: {k['content']}" for k in general)
           messages.insert(0, {"role": "system", "content": f"你已掌握的经验知识：\n{knowledge_text}"})

       if experience:
           exp_text = "\n".join(f"- {e['content']}" for e in experience)
           messages.insert(0, {"role": "system", "content":
               f"以下是类似查询的历史成功案例，参考其查询方式：\n{exp_text}"})
   ```

**测试**：
- Mock `search_relevant` 验证两路并行调用参数正确
- 验证 experience 召回失败不影响 general 召回
- 验证注入时 general 和 experience 分离为两个 system message
- 验证经验注入的前缀为"历史成功案例"
- 验证 general 和 experience 都为空时返回 None

---

## 六、验证方案

### 6.1 静态层验证

| 检查项 | 预期 |
|-------|------|
| 3 种模式 | description 包含 "summary / detail" + ">200行自动导出" |
| 6 个 group_by | description 包含 "shop/platform/product/supplier/warehouse/status" |
| 3 个 time_col | description 包含 "pay_time/doc_created_at/consign_time" |
| 平台列表 | description 包含 "淘宝" "拼多多" "抖音" 等 |
| few-shot 示例 | description 包含 5 个 query→effect 示例 |
| "不要用于" | description 包含 erp_execute / web_search / search_knowledge 的边界 |
| 返回格式 | description 包含 ">200行自动导出" |
| 字段分类 | description 包含 "可查询信息：单据基础/时间/商品/..." |
| token 预算 | description < 400 token |

### 6.2 种子覆盖验证

- `load_seed_knowledge()` 导入后，`knowledge_nodes` 表中 category=experience 且 source=seed 的记录 = 19 条
- 查询"按仓库统计入库"→ `search_relevant` 命中种子 #5
- 查询"各平台退货率"→ `search_relevant` 命中种子 #12

### 6.3 动态案例层验证

1. 首次查询"各平台退货率"→ 种子 #12 被召回 → 主 Agent 参考种子编排
2. 成功后 experience_recorder 写入新 routing_pattern（detail 含参数）
3. 再次查询"各店铺退货率"→ 召回种子 #12 + 上次成功案例 → 主 Agent 正确使用 group_by=shop

### 6.4 回归验证

- 前端测试全绿
- 后端测试全绿
- erp_agent benchmark（如有）准确率不降

---

## 七、不做的事

| 方案 | 为什么不做 |
|------|-----------|
| ToolRegistry 通用注册中心 | 只有 1 个子 Agent，简单工具不需要注册框架 |
| agent_card() 独立中间层 | 结构化数据已合并到 get_capability_manifest()，不需要再包一层（评审结论） |
| 运行时动态发现（discover_capabilities） | 大厂都是静态声明，工具 <20 个不需要搜索 |
| 主 Agent 开放 erp_api_search | erp_agent 内部能用，主 Agent 通过 query 传意图即可 |
| 预设编排模式（7 种模式写死） | 大厂证明：完整的工具描述 + LLM 推理 = 自动编排 |
| BaseAgent 抽象基类 | 当前 ERPAgent 是唯一子 Agent，加基类是提前抽象 |
| MCP 协议接入 | 当前系统是单进程架构，MCP 解决的是跨进程问题 |
| 参数 schema 化（mode/group_by 变成独立参数） | erp_agent 的 query 参数由内部 LLM 提取，比暴露 schema 更准 |

---

## 八、风险与边界

| 风险 | 应对 | 验证方式 |
|------|------|---------|
| 描述膨胀增加 token 消耗 | 控制在 400 token 内（约 600 字） | 单测检查 `len(description) / 2.5 < 400` |
| 经验召回噪声 | `min_confidence=0.6` + `limit=2` | Mock 测试验证过滤条件 |
| plan_builder 常量暴露后被误用 | `VALID_` 前缀 + `frozenset` 不可变 | 类型注解 + 代码审查 |
| 经验冷启动 | 19 条种子覆盖 27 个能力点，confidence=0.9 优先召回 | 覆盖矩阵验证 |
| group_by=product 新维度提取不准 | prompt 加 few-shot 示例 | `test_plan_builder.py` 新用例 |
| group_by 类型修复影响现有逻辑 | `_sanitize_params` str→list 转换 | 新旧两种输入格式都加单测 |
| 经验 detail 改进后占用更多存储 | 改进后约 50-80 字 | 实际验证不超过 100 字 |
| fields 参数提取不准（LLM 选错字段名） | plan_builder prompt 按分类列出字段+中文名，LLM 可精确映射 | `_sanitize_params` 白名单校验 + 单测 |
| 字段分类注入增加 token | ~50 token（10 个分类名一行），占比 0.15% | 实测验证 |

---

## 九、文件改动清单

| 文件 | 改动类型 | 行数估计 | Phase | 说明 |
|------|---------|---------|-------|------|
| `backend/services/agent/plan_builder.py` | 改造 | ~75 行 | Phase 1 | prompt 补全 + group_by 类型修复 + fields 参数提取 + 导出常量 + get_capability_manifest |
| `backend/services/agent/erp_agent.py` | 新增+改造 | ~80 行 | Phase 1+2 | 经验 detail 改进(P1) + build_tool_description(P2) |
| `backend/data/seed_knowledge.json` | 追加 | ~200 行 | Phase 1 | 19 条 experience 种子数据 |
| `backend/config/chat_tools.py` | 改造 | ~20 行 | Phase 2 | erp_agent description 改为自动生成 |
| `backend/services/handlers/chat_context_mixin.py` | 改造 | ~30 行 | Phase 3 | 两路并行召回 + tag 分离注入 |
| `backend/tests/test_plan_builder.py` | 新增 | ~40 行 | Phase 1 | group_by 类型转换 + manifest 完整性 |
| `backend/tests/test_erp_agent.py` | 新增 | ~40 行 | Phase 2 | description 内容 + token 预算 |
| `backend/tests/test_chat_context_mixin.py` | 新增 | ~25 行 | Phase 3 | 并行召回 + 分离注入 |

**总计**：8 个文件，~510 行改动（含测试 + 种子数据）

### 代码审核确认项

| 检查项 | 结果 |
|-------|------|
| 循环导入风险（chat_tools→erp_agent） | ✅ 安全。`_build_common_tools()` 运行时才调用 |
| token 预算影响（~400 描述 + ~200 经验） | ✅ 安全。context_max_tokens=32000，增加 600 占 1.9% |
| `_should_skip_knowledge` 是否拦截 ERP 查询 | ✅ 安全。ERP 查询不匹配排除正则 |
| experience_recorder 配额 | ✅ 安全。ROUTING_PATTERN_MAX=400，超出自动淘汰 |
| `_fetch_knowledge` 返回类型 | ✅ 保持 `list | None` 不变，通过 `_source` tag 分离 |
| 种子与现有 seed_knowledge.json 兼容 | ✅ 格式一致，category=experience 在白名单中 |

---

## 十、未来扩展

当系统演进到多个子 Agent 时（如 ScheduledTaskAgent、ReportAgent），扩展路径：

1. 每个子 Agent 实现 `get_capability_manifest()` + `build_tool_description()` 方法
2. `chat_tools.py` 调用各 Agent 的 `build_tool_description()` 生成描述
3. manifest 的结构化数据可以通过 API 暴露（如 `/api/agent-capabilities`）
4. 如果子 Agent 超过 5 个，可以提取 `BaseAgent` 基类统一 manifest 接口
5. 如果工具超过 20 个，再引入 BigTool 式的语义搜索发现（`erp_api_search` 开放给主 Agent）
6. `ExperienceRecorder` 已支持多 Agent（`writer` 参数），无需改动
7. 种子数据按 Agent 分组追加到 `seed_knowledge.json`

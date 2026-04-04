# 技术方案：工具注册七层架构

> **状态**：方案设计中  
> **日期**：2026-04-03  
> **等级**：A级（核心架构设计）  
> **参考**：Claude Code 7 层工具架构 + OpenAI Function Calling 最佳实践

## 一、背景

单循环 Agent 架构（Phase 1+2）已完成工具循环机制，但 benchmark 测试准确率仅 40%。
根因：AI 拿到 26 个工具后缺乏使用指引，不知道工具之间的依赖关系和调用顺序。

旧架构的高准确率来自 Phase1/Phase2 的硬编码控制流 + 系统提示词 + 工具选择器的关键词过滤。
这些"隐含规则"散落在 phase_tools.py、erp_tools.py、tool_selector.py、api_search.py 中，
共计 28 条关键规则，在新架构中完全缺失。

## 二、七层架构总览

```
层1: 工具定义（Tool Definition）
  → 每个工具的 schema + 描述 + 交叉引用 + 元数据

层2: 工具收集 + 过滤（Tool Collection & Filtering）
  → 按企业配置、用户权限动态裁剪工具列表

层3: 工具加载策略（Tool Loading Strategy）
  → 核心工具直接加载，ERP 细分工具按需搜索发现

层4: 描述动态生成（Dynamic Description）
  → 工具描述根据企业配置、对话上下文动态调整

层5: 系统提示词（System Prompt Guidance）
  → 全局工具使用规则，从旧架构 28 条规则中提取核心

层6: 结果校验 + 截断（Result Validation & Truncation）
  → 工具返回结果的大小控制，防止撑爆 context

层7: 工具安全 + 分组（Safety & Grouping）
  → 安全级别 + 并发标记 + 业务分组
```

## 三、各层详细设计

### 层1：工具定义（Tool Definition）

**现状**：工具有 name/parameters/description，但 description 太简单。
**目标**：每个工具的 description 包含完整的使用指引 + 交叉引用。

**设计原则**（参考 OpenAI 最佳实践）：
- 描述像合同：目的行 + 使用场景 + 参数说明 + 交叉引用
- 用户意图映射："用户说 XX 时用这个工具"
- 明确"不该用我"的场景："模糊编码先用 local_product_identify"

**改动**：

```python
# 改前（erp_local_tools.py）
"按商品编码查询库存信息"

# 改后
"按商品精确编码查询库存（可售/锁定/在途/各仓分布）。毫秒级响应。\n"
"⚠ 需要精确编码（如 SHOE-001），用户给模糊名称/简称时，"
"先调 local_product_identify 确认编码再用本工具。\n"
"返回同步警告时，考虑 trigger_erp_sync 或改用 erp_product_query 远程查询。"
```

**每个工具 description 必须包含**：
1. 做什么（一句话）
2. 什么时候用（用户意图映射）
3. 什么时候不用 / 先用别的（交叉引用）
4. 注意事项（如果有）

**涉及文件**：
- config/erp_local_tools.py — 11 个本地工具
- config/erp_tools.py — 8 个远程工具 + erp_execute
- config/chat_tools.py — 5 个通用工具（web_search 等）
- config/crawler_tools.py — 1 个爬虫工具
- config/code_tools.py — 1 个代码执行工具

### 层2：工具收集 + 过滤（Tool Collection & Filtering）

**现状**：get_chat_tools() 全量返回 26 个工具。
**目标**：按企业配置动态过滤，减少 AI 选择空间。

**过滤规则**：

| 条件 | 过滤行为 |
|------|---------|
| 散客（无 org_id） | 去掉所有 ERP 工具（远程+本地），只留通用 5 个 |
| 企业无 ERP 凭证 | 去掉 ERP 远程工具，保留本地工具 |
| 企业未开通爬虫 | 去掉 social_crawler |
| 企业未开通代码执行 | 去掉 code_execute |

**实现**：

```python
def get_chat_tools(
    org_id: str | None = None,
    org_features: dict | None = None,
) -> List[Dict[str, Any]]:
    tools = []
    
    if org_id:
        tools.extend(build_erp_tools())  # ERP 远程+本地
    
    if org_features and org_features.get("crawler_enabled"):
        tools.extend(build_crawler_tools())
    
    if org_features and org_features.get("code_enabled", True):
        tools.extend(build_code_tools())
    
    tools.extend(_build_common_tools())  # 搜索/知识库/图片/视频（始终加载）
    
    return tools
```

**涉及文件**：config/chat_tools.py

### 层3：工具加载策略（Tool Loading Strategy）— ToolSearch 模式

**现状**：26 个工具全部塞进 messages，每次请求消耗 ~3000 tokens。
**目标**：核心工具直接加载，其余工具通过 erp_api_search 按需发现（类似 Claude Code 的 ToolSearch）。

**策略**：只有核心工具的 schema 传给 LLM，其余工具 AI 不知道有——需要时通过搜索发现。

```
直接加载（schema 传给 LLM，~8 个）：
  - erp_api_search（ToolSearch 入口，最重要）
  - local_product_identify（编码识别，高频）
  - local_stock_query（库存查询，高频）
  - local_order_query（订单查询，高频）
  - local_global_stats（全局统计，高频）
  - search_knowledge（知识库）
  - web_search（互联网搜索）
  - generate_image / generate_video（图片/视频生成）

按需发现（schema 不传给 LLM，AI 搜索后才知道）：
  - 远程 ERP 工具：erp_product_query, erp_trade_query, erp_warehouse_query...
  - 低频本地工具：local_purchase_query, local_aftersale_query, local_doc_query...
  - 专项工具：social_crawler, code_execute, trigger_erp_sync, erp_execute
```

**工作流程**：

```
用户: "采购单到货了没"
  Turn1: AI 不知道有 local_purchase_query（schema 没传）
       → AI 调 erp_api_search("采购到货")
       → 返回: "推荐 local_purchase_query，必填参数: product_code"
  Turn2: AI 现在知道了 → 调 local_purchase_query(product_code="XX")
       → 返回采购到货数据
  Turn3: AI 回答用户

用户: "YSL01还有多少库存"
  Turn1: AI 直接看到 local_product_identify + local_stock_query（核心工具，直接加载）
       → 并行调用两个工具
  Turn2: AI 回答用户
```

**优势**：
- 高频场景（库存/订单/商品）：核心工具直接可用，不多一轮搜索
- 低频场景（采购/售后/调拨）：多一轮搜索，但省了每次请求 ~1500 tokens
- 为未来 Agent 扩展预留：工具越多，ToolSearch 优势越明显

**实现**：

```python
# 核心工具（直接传给 LLM）
_CORE_TOOLS: Set[str] = {
    "erp_api_search", "local_product_identify",
    "local_stock_query", "local_order_query", "local_global_stats",
    "search_knowledge", "web_search",
    "generate_image", "generate_video",
}

def get_chat_tools(org_id=None, org_features=None) -> List[Dict]:
    all_tools = _collect_all_tools(org_id, org_features)
    # 只返回核心工具的 schema
    return [t for t in all_tools if t["function"]["name"] in _CORE_TOOLS]

def get_all_tool_names() -> Set[str]:
    """ToolExecutor 需要知道所有工具名（即使 schema 没传给 LLM）"""
    return {t["function"]["name"] for t in _collect_all_tools()}
```

**关键：动态 Schema 注入（方案 C）**

LLM 必须看到工具的完整 schema 才能正确传参。所以不是"AI 搜索后直接调用"，
而是 **ChatHandler 在下一轮请求中动态加入被发现的工具 schema**：

```python
# ChatHandler 工具循环中
discovered_tools: Set[str] = set()  # 通过 erp_api_search 发现的工具名

for turn in range(MAX_TOOL_TURNS):
    # 动态构建本轮 tools 列表：核心工具 + 已发现的工具
    current_tools = get_core_tools(org_id)
    if discovered_tools:
        current_tools.extend(get_tools_by_names(discovered_tools))
    stream_kwargs["tools"] = current_tools

    # ... LLM 流式生成 + 执行工具 ...

    # 工具执行完毕后，扫描结果中提到的新工具名
    for tc, result_text, is_error in tool_results:
        if tc["name"] == "erp_api_search" and not is_error:
            # 从 erp_api_search 返回结果中解析工具名
            new_tools = extract_tool_names_from_search_result(result_text)
            discovered_tools.update(new_tools)
```

**与 ERP 三层架构的完美吻合**：

```
Turn1: AI 调 erp_api_search("订单")
  → 返回: "推荐 erp_trade_query:order_list，必填参数: order_id"
  → ChatHandler 解析出 "erp_trade_query"，加入 discovered_tools

Turn2: tools 列表动态扩展，AI 看到 erp_trade_query 的完整 schema
  → 层2：AI 传 action="order_list"（选 action）
  → 工具返回参数文档（层2 → 层3 衔接）

Turn3: AI 传 action="order_list" + params={order_id: "xxx"}
  → 执行查询 + 返回结果 + generate_param_hints（层3 信息）
  → AI 回答用户

高频场景（库存/订单/商品）：
  核心工具直接可用，不需要搜索，1-2 轮搞定

低频场景（采购/调拨/售后）：
  多一轮搜索 + schema 注入，2-3 轮搞定
```

**辅助函数**：

```python
def extract_tool_names_from_search_result(result: str) -> Set[str]:
    """从 erp_api_search 返回结果中提取工具名"""
    import re
    # 匹配格式: "- erp_trade_query:order_list" 或 "推荐 erp_trade_query"
    pattern = r'(erp_\w+|local_\w+|social_crawler|code_execute|trigger_erp_sync)'
    return set(re.findall(pattern, result))

def get_tools_by_names(names: Set[str]) -> List[Dict]:
    """根据工具名获取完整 schema"""
    all_tools = _collect_all_tools()
    return [t for t in all_tools if t["function"]["name"] in names]
```

**涉及文件**：
- config/chat_tools.py — get_core_tools(), get_tools_by_names(), extract_tool_names_from_search_result()
- services/handlers/chat_handler.py — 工具循环中维护 discovered_tools + 动态构建 tools 列表

### 层4：描述动态生成 + 上下文注入（Dynamic Description & Context Injection）

**现状**：工具描述是静态字符串，工具循环每轮看到的信息一样。
**目标**：根据企业配置 + 工具循环执行上下文动态调整，减少冗余调用。

**两层动态**：

#### 4A. 企业配置维度（请求级，每次请求生成一次）

| 维度 | 变化 |
|------|------|
| 企业有 ERP 凭证 | ERP 工具描述完整 |
| 企业无 ERP 凭证 | ERP 远程工具不加载，本地工具描述加"数据来自同步" |
| 企业开通爬虫 | social_crawler 加载 |

#### 4B. 工具循环上下文维度（轮次级，每轮 turn 更新）

在工具循环中维护一个 `tool_context` 字典，每轮执行后更新，注入到下一轮的系统提示词：

```python
tool_context = {}  # 工具循环中累积的上下文

# Turn 1 执行完 local_product_identify 后：
tool_context["identified_codes"] = {"YSL01": "YSL01-RED-M"}

# Turn 2 的系统提示词自动追加：
"已识别编码: YSL01 → YSL01-RED-M，后续查询直接使用精确编码，无需再次识别。"

# Turn 1 执行完 local_stock_query 返回同步警告后：
tool_context["sync_warnings"] = ["stock数据延迟5分钟"]

# Turn 2 的系统提示词自动追加：
"⚠ 库存数据可能延迟，如需实时数据请用 erp_product_query 远程查询。"
```

**实现**：

```python
class ToolLoopContext:
    """工具循环上下文，跨轮次累积信息"""

    def __init__(self):
        self.identified_codes: Dict[str, str] = {}   # 模糊编码 → 精确编码
        self.sync_warnings: List[str] = []            # 同步警告
        self.used_tools: List[str] = []               # 已使用的工具
        self.failed_tools: List[str] = []             # 执行失败的工具

    def update_from_result(self, tool_name: str, result: str, is_error: bool):
        """从工具执行结果中提取上下文"""
        self.used_tools.append(tool_name)
        if is_error:
            self.failed_tools.append(tool_name)
        # 提取 identify 结果
        if tool_name == "local_product_identify" and not is_error:
            # 解析结果中的编码映射
            ...
        # 提取同步警告
        if "⚠" in result and "同步" in result:
            self.sync_warnings.append(result.split("⚠")[1][:50])

    def build_context_prompt(self) -> str:
        """生成当前轮次的上下文提示，注入系统提示词"""
        lines = []
        if self.identified_codes:
            codes = ", ".join(f"{k}→{v}" for k, v in self.identified_codes.items())
            lines.append(f"已识别编码: {codes}（直接使用，无需再次识别）")
        if self.sync_warnings:
            lines.append("⚠ 数据同步延迟中，如需实时数据请用远程 erp_* 工具")
        if self.failed_tools:
            lines.append(f"上轮失败工具: {', '.join(self.failed_tools)}，考虑换其他工具")
        return "\n".join(lines)
```

**为未来 Agent 预留**：
- `ToolLoopContext` 可以跨 Agent 传递（父 Agent 的上下文共享给子 Agent）
- 子 Agent 的执行结果可以回写到父 Agent 的 context

**涉及文件**：
- config/chat_tools.py — `ToolLoopContext` 类
- services/handlers/chat_handler.py — 在工具循环中维护 context

### 层5：系统提示词（System Prompt Guidance）

**现状**：ChatHandler 没有工具使用指引。
**目标**：从旧架构 28 条规则中提取 5-6 条核心全局规则。

**规则来源映射**：

| 旧架构位置 | 规则 | 放哪里 |
|-----------|------|--------|
| phase_tools.py PHASE1_SYSTEM_PROMPT | ERP关键词列表 | 不需要（AI 看工具描述就行） |
| phase_tools.py BASE_AGENT_PROMPT | 多次调用同一工具 | **→ 系统提示词** |
| erp_tools.py ERP_ROUTING_PROMPT §1 | 本地优先于远程 | **→ 系统提示词** |
| erp_tools.py §4 | 先identify再查询 | **→ 系统提示词 + 工具描述** |
| erp_tools.py §3 | 两步查询协议 | **→ 工具描述** |
| erp_tools.py §5 | 时间语义 | **→ 工具描述** |
| erp_tools.py §6 | 销量=sum(num) | **→ 场景指南** |
| erp_tools.py §9 | 跨工具接力 | **→ 场景指南** |
| tool_selector.py | 关键词过滤 | 不需要（AI 自己选） |
| api_search.py _SCENARIO_DOCS | 8 个场景指南 | **→ erp_api_search 已有** |

**全局系统提示词内容**（~200 tokens）：

```python
TOOL_SYSTEM_PROMPT = """
## 工具使用规则

1. **编码识别优先**：用户提到商品名称/简称/模糊编码时，先调 local_product_identify 确认精确编码，再用对应查询工具。同一编码每次对话只需识别一次。

2. **本地优先远程**：local_* 工具查本地数据库（毫秒级），erp_* 工具查远程API（秒级）。优先用本地工具，本地查不到或需要实时数据时再用远程。

3. **不确定先搜索**：不确定用哪个工具或哪个 action 时，先调 erp_api_search 搜索相关工具文档。

4. **多维度采集**：可以多次调用同一工具（不同参数）采集多维数据，也可以组合多个工具完成复杂需求。

5. **两步查询**：远程 ERP 工具（erp_* 开头）使用两步调用：第一次只传 action 获取参数文档，第二次按文档传入 params 执行查询。已确定参数时可跳过第一步。

6. **数据不新鲜时**：本地工具返回同步警告（⚠），先 trigger_erp_sync 触发同步再重查，或改用远程 erp_* 工具查实时数据。
"""
```

**涉及文件**：config/chat_tools.py 新增 `get_tool_system_prompt()`

### 层6：结果校验 + 摘要引导（Result Validation & Summary）

**现状**：远程 ERP 工具有 4000 字符截断，本地工具无截断控制。
**问题**：local_* 工具返回 100 条记录可能 5000+ 字符，塞进 messages 浪费 context。

**方案**：利用现有基础设施，不新建表，不存储——超长结果摘要 + 引导 AI 用 code_execute 分析。

**现有基础设施**：
- 远程 ERP → dispatcher._GLOBAL_CHAR_BUDGET = 4000（已有截断）✅
- 本地查询 → SQL LIMIT 100（有上限但无字符截断）⚠️ 需要加摘要
- 沙箱 → erp_query_all() 全量翻页 + pandas 分析 + truncate_result 输出控制 ✅
- 完整数据始终在本地数据库/远程 API 中，不需要额外存储 ✅

**只需要做一件事**：local_* 工具返回超过阈值时，加摘要提示。

```python
TOOL_RESULT_SUMMARY_THRESHOLD = 4000  # 与 dispatcher 对齐
TOOL_RESULT_SUMMARY_PREVIEW = 2000    # 摘要保留前 N 字符

def summarize_if_needed(tool_name: str, result: str) -> str:
    """大结果自动摘要，引导 AI 用 code_execute 做全量分析"""
    if len(result) <= TOOL_RESULT_SUMMARY_THRESHOLD:
        return result

    preview = result[:TOOL_RESULT_SUMMARY_PREVIEW]
    return (
        f"{preview}\n\n"
        f"⚠ 结果较多（{len(result)}字符），以上为部分数据。\n"
        f"如需全量数据分析/导出，可用 code_execute 调用 erp_query_all() 获取完整数据。"
    )
```

**AI 看到摘要后的两条路径**：

```
路径A：摘要够用
  AI 基于前 2000 字符的数据直接回答用户 → 完成

路径B：需要全量分析
  AI 调 code_execute：
    data = await erp_query_all("erp_trade_query", "outstock_query", {"start_date": "2026-04-01"})
    df = pd.DataFrame(data["list"])
    print(f"平均金额: {df['payment'].mean():.2f}")
    print(f"总订单: {len(df)}")
  → 沙箱内完成全量分析，truncate_result 控制输出 → 回答用户

路径C：需要导出
  AI 调 code_execute 生成 CSV → 返回文件链接
```

**涉及文件**：services/handlers/chat_tool_mixin.py — 在 _execute_single_tool 返回前调用 summarize_if_needed()

### 层7：工具安全 + 分组（Safety & Grouping）

**现状**：已有 safety_level（safe/confirm/dangerous）和 is_concurrency_safe。
**扩展**：加业务分组，用于工具过滤和上下文注入。

**分组设计**：

```python
class ToolGroup(str, Enum):
    """工具业务分组"""
    ERP_LOCAL = "erp_local"     # 本地 ERP 查询（毫秒级）
    ERP_REMOTE = "erp_remote"   # 远程 ERP API（秒级）
    ERP_WRITE = "erp_write"     # ERP 写操作
    SEARCH = "search"           # 搜索类（知识库/互联网/ERP文档）
    MEDIA = "media"             # 图片/视频生成
    CRAWLER = "crawler"         # 社交平台爬虫
    CODE = "code"               # 代码执行
```

**用途**：
- 过滤：散客只加载 SEARCH + MEDIA 分组
- 系统提示词：动态生成"你有以下类型的工具可用"
- 日志：按分组统计工具使用频率

**涉及文件**：config/chat_tools.py

## 四、实现计划

### 第一批：核心（直接影响准确率）

| 序号 | 层 | 任务 | 文件 | 说明 |
|------|---|------|------|------|
| 1 | 层5 | 全局系统提示词 | config/chat_tools.py | get_tool_system_prompt() — 6 条核心规则 |
| 2 | 层1 | 工具 description 交叉引用 | erp_local_tools.py + erp_tools.py + chat_tools.py | 每个工具加"先用X再用Y"引导 |
| 3 | 层3 | ToolSearch 加载策略 | config/chat_tools.py | 8 核心直接加载 + 18 按需搜索 |
| 4 | 层5 | ChatHandler 注入系统提示词 | handlers/chat_handler.py | _stream_generate 里 messages 加工具指引 |
| 5 | 验证 | benchmark 准确率测试 | scripts/test_tool_loop_benchmark.py | 目标 ≥85% |

### 第二批：完善（提升体验）

| 序号 | 层 | 任务 | 文件 | 说明 |
|------|---|------|------|------|
| 6 | 层2 | 工具过滤（散客/企业配置） | config/chat_tools.py | 散客不加载 ERP 工具 |
| 7 | 层7 | 工具分组 ToolGroup | config/chat_tools.py | 业务分组枚举 |
| 8 | 层4 | ToolLoopContext 上下文注入 | config/chat_tools.py + chat_handler.py | 跨轮次上下文累积 |

### 第三批：健壮性（防止 context 溢出）

| 序号 | 层 | 任务 | 文件 | 说明 |
|------|---|------|------|------|
| 9 | 层6 | 结果摘要+存储 | chat_tool_mixin.py + 数据库 | 大结果存 DB，AI 收摘要 |
| 10 | 层4 | 动态描述（企业配置维度） | config/chat_tools.py | 根据凭证动态调整描述 |

### 验收标准

- benchmark 准确率 ≥85%（当前 40%）
- 高频场景（库存/订单/商品）一轮选对工具
- 低频场景通过 erp_api_search 搜索后两轮完成
- 工具结果不撑爆 context（大结果自动摘要）

## 五、与现有架构的关系

```
单循环 Agent 架构升级（已完成）：
  Phase 1: ERP 返回优化 ✅
  Phase 2: ChatHandler 工具循环 ✅
  
工具注册七层架构（本方案）：
  → Phase 2 的补充，让工具循环真正好用
  → 必须在 Phase 3（删除旧路由）之前完成
  
Phase 3: 删除旧路由（依赖本方案完成后再做）
```

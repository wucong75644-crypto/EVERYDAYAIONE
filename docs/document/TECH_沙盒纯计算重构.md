# 沙盒纯计算重构方案

> 日期：2026-04-07 | 状态：✅ 方案已确认

## 一、问题

当前 `code_execute` 沙盒注册了 `erp_query`/`erp_query_all` 等数据获取函数，导致：
- Agent 跳过 `local_*` 工具，直接在沙盒里调远程 API
- 沙盒成为"万能工具"，工具优先级 `local > erp > code_execute` 形同虚设
- Agent 幻觉参数名（如 `payTimeStart`），在沙盒里反复试错

**行业对标**：OpenAI Code Interpreter / Claude Code 的沙盒都不注册 API 调用能力，只做纯计算。

## 二、目标

沙盒 = 纯计算引擎。数据获取必须走 Agent 工具层（local 优先）。

```
改前：Agent → code_execute → erp_query_all → 远程API → 计算 → 输出
改后：Agent → local_*/erp_*/fetch_all_pages 工具 → 数据存staging → code_execute → 读staging → 计算 → 输出
```

## 三、沙盒能力变更

| 函数 | 改前 | 改后 | 理由 |
|------|:---:|:---:|------|
| erp_query | ✅ | ❌ | 数据获取走工具层 |
| erp_query_all | ✅ | ❌ | 翻页能力上移为独立工具 `fetch_all_pages` |
| web_search | ✅ | ❌ | 走 crawler 工具 |
| search_knowledge | ✅ | ❌ | 走知识库工具 |
| write_file | ✅ | ❌ | 走文件工具 |
| list_dir | ✅ | ❌ | 走文件工具 |
| get_persisted_result | ✅ | ❌ | 改为 staging 机制 |
| **read_file** | ✅ | ✅（限 staging 目录） | 读取预获取的数据 |
| **upload_file** | ✅ | ✅ | 输出计算结果 |
| pandas/math/datetime/Decimal | ✅ | ✅ | 核心计算能力 |

## 四、独立翻页工具 `fetch_all_pages`（新增）

### 4.1 设计原则

翻页是独立的可组合工具，不是查询工具的内置参数。遵循 Unix 哲学——每个工具只做一件事，通过组合完成复杂需求。

```
已有的组合模式：
  erp_api_search   → 发现工具 → 自动注入       （发现+注入）
  两步查询          → Step1拿参数文档 → Step2执行 （协议组合）
  编码识别回退      → local查不到 → 自动API兜底   （降级组合）

新增的组合模式：
  fetch_all_pages  → 包装任意查询工具 → 自动翻页  （翻页组合）
```

### 4.2 工具定义

```python
{
    "name": "fetch_all_pages",
    "description": (
        "全量翻页工具。包装任意 erp_* 远程查询工具，自动翻页拉取全部数据。"
        "适合：导出Excel、全量数据分析、跨数据源关联等需要完整数据的场景。"
        "结果自动存为 staging 文件，返回文件路径。"
        "配合 code_execute 使用：先用本工具拿全量数据，再用 code_execute 计算/导出。"
        "⚠ 翻页耗时较长（100条/页，每页约1秒），请根据预估数据量合理设置 max_pages。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "要翻页的查询工具名（如 erp_trade_query）"
            },
            "action": {
                "type": "string",
                "description": "操作名（如 order_list）"
            },
            "params": {
                "type": "object",
                "description": "查询参数（与直接调用该工具时的 params 相同）"
            },
            "page_size": {
                "type": "integer",
                "description": "每页条数（默认100，最小20，快麦API限制）"
            },
            "max_pages": {
                "type": "integer",
                "description": "最大翻页数（默认200）。预估数据量少时设小可加速，如预估500条设max_pages=5"
            },
        },
        "required": ["tool", "action"],
    },
}
```

### 4.3 执行器内部逻辑

复用现有 `sandbox/functions.py:erp_query_all` 的翻页逻辑：

```python
# tool_executor.py 内部
async def _execute_fetch_all_pages(self, args):
    tool_name = args["tool"]
    action = args["action"]
    params = args.get("params", {})
    page_size = max(args.get("page_size", 100), 20)  # Agent可控，最小20
    max_pages = args.get("max_pages", 200)            # Agent可控，默认200

    all_items = []
    semaphore = asyncio.Semaphore(10)  # API并发限流

    for page in range(1, max_pages + 1):
        async with semaphore:
            data = await dispatcher.execute_raw(
                tool_name, action, {**params, "page": page, "page_size": page_size}
            )

        if "error" in data:
            if all_items:  # 已有部分数据，返回已拉到的 + 警告
                break
            return f"❌ 查询失败: {data['error']}"

        items = extract_list(data)
        all_items.extend(items)

        # 进度通知（通过 WebSocket）
        await self._notify_progress(f"翻页中 {page}/{max_pages}，已获取 {len(all_items)} 条")

        if len(items) < page_size:  # 最后一页
            break

    # 自动存 staging
    path = save_to_staging(task_id, all_items)
    return f"[数据已暂存] {path}\n共 {len(all_items)} 条记录。"
```

### 4.4 参数策略

| 参数 | 谁控制 | 默认值 | 说明 |
|------|-------|-------|------|
| page_size | Agent | 100 | 每页条数，最小 20（快麦 API 限制） |
| max_pages | Agent | 200 | 最大翻页数，Agent 根据预估数据量设置 |
| page | 执行器 | 自动递增 | 从 1 开始，返回数 < page_size 时停止 |
| semaphore | 执行器 | 10 并发 | API 限流，防止打爆快麦 |

工具参数透明，Agent 可根据场景优化（如预估 200 条 → max_pages=2，省时间）。

### 4.5 与现有工具的关系

```
单条/少量数据：
  Agent → erp_trade_query(action, params)     → 直接返回上下文

全量数据：
  Agent → fetch_all_pages(tool, action, params) → 自动翻页 → staging 文件
  Agent → code_execute → read_file(staging) → 计算/导出
```

查询工具保持简单（单页查询），翻页工具独立组合。两者各司其职。

### 4.6 与两步协议的衔接

erp_* 远程工具有两步协议（Step1 拿参数文档 → Step2 传参执行）。Agent 用 `fetch_all_pages` 前，先通过两步协议确认参数：

```
Step 1: erp_trade_query(action="order_list")        → 拿到参数文档
Step 2: fetch_all_pages(tool="erp_trade_query", ...) → 按文档传参，全量翻页
```

### 4.7 参数映射

`fetch_all_pages` 内部调用 `dispatcher.execute_raw()`，参数映射（如 `start_date` → `startTime`）由 dispatcher 内部处理，无需额外转换。

## 五、Staging 机制（新增）

### 5.1 自动存储

`fetch_all_pages` 的结果自动存为 staging 文件：

```python
def save_to_staging(task_id: str, data: list) -> str:
    """存储全量数据到 staging 文件，返回路径"""
    path = f"staging/{task_id}/{tool_name}_{timestamp}.json"
    write_json(path, data)

    # 返回摘要+文件路径（Agent 看到的）
    return (
        f"[数据已暂存] {path}\n"
        f"共 {len(data)} 条记录。如需处理请调 code_execute，"
        f"用 read_file(\"{path}\") 读取数据。\n\n"
        f"前3条预览：\n{preview(data[:3])}"
    )
```

### 5.2 Staging 文件生命周期

- 创建：`fetch_all_pages` 执行完自动创建
- 读取：`code_execute` 沙盒内 `read_file(path)` 读取
- 清理：任务结束后定时清理（TTL 30分钟）
- 目录：`{workspace_root}/staging/{task_id}/`

### 5.3 read_file 安全限制

沙盒内 read_file 只允许读取 staging 目录（对标 OpenAI Code Interpreter 模式）：

```python
async def _safe_read_file(path: str, encoding: str = "utf-8") -> str:
    if not path.startswith("staging/"):
        return "❌ 沙盒内只能读取 staging 目录下的数据文件"
    return await _original_read_file(path, encoding)
```

## 六、典型场景对比

### 场景1：统计今天和昨天付款订单涨跌幅

**改前（错误流程）**：
```
Agent → code_execute → erp_query_all("erp_trade_query", "order_list", {payTimeStart: ...})
→ 幻觉参数名 → 反复重试 → 超时
```

**改后（正确流程）**：
```
Agent → local_global_stats(doc_type="order", time_type="pay_time",
          start_time="2026-04-07 00:00:00", end_time="2026-04-07 19:00:00")
Agent → local_global_stats(同上，日期换昨天)
Agent → 直接文字计算涨跌幅（无需 code_execute）
```

### 场景2：导出5000条订单到Excel

**改前**：
```
Agent → code_execute:
  orders = await erp_query_all("erp_trade_query", "order_list", {...})
  df = pd.DataFrame(orders["list"])
  buf = io.BytesIO(); df.to_excel(buf)
  url = await upload_file(buf.getvalue(), "订单.xlsx")
```

**改后**：
```
Agent → fetch_all_pages(tool="erp_trade_query", action="order_list", params={...})
     → 自动翻页50页 → staging/xxx/orders.json（5000条）
Agent → code_execute:
  data = json.loads(await read_file("staging/xxx/orders.json"))
  df = pd.DataFrame(data)
  buf = io.BytesIO(); df.to_excel(buf)
  url = await upload_file(buf.getvalue(), "订单.xlsx")
```

### 场景3：跨数据源关联（订单+库存）

**改后**：
```
Agent → fetch_all_pages(tool="erp_trade_query", action="order_list", params={...})
     → staging/xxx/orders.json
Agent → fetch_all_pages(tool="erp_product_query", action="stock_status", params={...})
     → staging/xxx/stock.json
Agent → code_execute:
  orders = json.loads(await read_file("staging/xxx/orders.json"))
  stock = json.loads(await read_file("staging/xxx/stock.json"))
  df_orders = pd.DataFrame(orders)
  df_stock = pd.DataFrame(stock)
  merged = df_orders.merge(df_stock, on="goodsNo")
  # ... 计算 ...
  url = await upload_file(buf.getvalue(), "关联报表.xlsx")
```

## 七、改动文件清单

| 文件 | 改动 | 复杂度 |
|------|------|:------:|
| `services/sandbox/functions.py` | 删除 erp_query/erp_query_all/web_search/search_knowledge/write_file/list_dir/get_persisted_result 注册，read_file 限制 staging 目录 | 中 |
| `config/code_tools.py` | 更新 code_execute 工具描述 + CODE_ROUTING_PROMPT | 低 |
| `config/erp_tools.py` | 更新 ERP_ROUTING_PROMPT；新增 fetch_all_pages 工具定义 | 中 |
| `services/agent/tool_executor.py` | 新增 fetch_all_pages 执行逻辑（复用 erp_query_all 翻页代码） | 中 |
| `services/agent/tool_result_envelope.py` | 新增 staging 存储逻辑 | 中 |
| `services/agent/erp_agent.py` | fetch_all_pages 加入可见工具列表 | 低 |
| `tests/test_sandbox_functions.py` | 更新测试用例 | 中 |
| `tests/test_code_tools.py` | 更新测试用例 | 低 |
| `tests/test_fetch_all_pages.py` | 新增 fetch_all_pages 测试 | 中 |

## 八、分阶段实施

### Phase 1：沙盒瘦身 + fetch_all_pages（核心）
1. sandbox/functions.py 删除数据获取函数注册
2. 新增 fetch_all_pages 工具定义和执行逻辑
3. code_tools.py 更新工具描述和 CODE_ROUTING_PROMPT
4. erp_tools.py 更新 ERP_ROUTING_PROMPT
5. erp_agent.py 将 fetch_all_pages 加入可见工具
6. 更新测试

**交付标准**：
- Agent 无法在 code_execute 中调用 erp_query（NameError）
- Agent 可通过 fetch_all_pages 获取全量数据

### Phase 2：Staging 机制
1. tool_result_envelope.py 新增 staging 存储
2. fetch_all_pages 结果自动存 staging
3. read_file 限制 staging 目录
4. staging 文件清理（TTL 30分钟）

**交付标准**：全量数据自动存 staging，code_execute 能通过 read_file 读取

### Phase 3：提示词调优 + 端到端测试
1. 测试 10 个典型场景（统计/对比/导出/关联）
2. 根据 Agent 实际行为微调提示词
3. 知识库经验清理（删除引导用 code_execute 查数据的经验）

**交付标准**：10 个场景 Agent 全部走正确路径

## 九、风险与缓解

| 风险 | 概率 | 缓解 |
|------|:---:|------|
| Agent 不会用 staging 模式 | 中 | 提示词明确示例 + staging 返回消息自带使用提示 |
| Agent 不会用 fetch_all_pages | 中 | 工具描述清晰 + ERP_ROUTING_PROMPT 场景路由引导 |
| staging 文件占用磁盘 | 低 | TTL 30分钟自动清理 |
| 老知识库经验干扰 | 中 | Phase 3 清理 |

## 十、预估工作量

| Phase | 文件数 | 内容 |
|-------|:-----:|------|
| Phase 1 | 6 | 沙盒瘦身 + fetch_all_pages + 提示词 |
| Phase 2 | 3 | staging 机制 |
| Phase 3 | - | 测试调优 |

## 十一、已确认问题

1. ~~**翻页问题**~~：✅ 独立工具 `fetch_all_pages`，可组合任意 erp_* 查询工具
2. ~~**staging 阈值**~~：✅ 不设阈值——`fetch_all_pages` 结果统一存 staging 文件，替代截断，数据完整保留
3. ~~**read_file 范围**~~：✅ 只限 staging 目录，对标 OpenAI Code Interpreter——沙盒只能读工具层预准备的数据
4. ~~**fetch_all 设计**~~：✅ 独立工具而非参数，遵循 Unix 哲学——每个工具单一职责，组合使用
5. ~~**参数透明性**~~：✅ page_size / max_pages 暴露给 Agent，有合理默认值，Agent 自行根据场景优化
6. ~~**API 并发控制**~~：✅ 执行器内部 Semaphore(10) 限流
7. ~~**翻页中途失败**~~：✅ 返回已拉到的数据 + 警告，不丢弃
8. ~~**进度通知**~~：✅ 每页通过 WebSocket 通知用户当前进度
9. ~~**两步协议衔接**~~：✅ Agent 先用 erp_* 工具拿参数文档，再用 fetch_all_pages 传参翻页
10. ~~**参数映射**~~：✅ dispatcher.execute_raw 内部处理，无需额外转换

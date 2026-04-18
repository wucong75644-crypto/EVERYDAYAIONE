# 多Agent单一职责重构方案
**版本 2.2 · 2026-04-16（第五轮架构评审修订 · 终版）**

---

## 1. 问题总览

当前 ERPAgent 违反单一职责原则，一个 Agent 同时承担 6 项职责：
1. **查询路由** — 选择用哪个工具查数据
2. **参数构造** — 构建查询参数
3. **计算编排** — 调 code_execute 做聚合/对比/导出
4. **文件管理** — 跟踪 staging 文件
5. **跨域关联** — 缺货+采购单时间对比等多域查询
6. **经验记录** — 路由模式/失败模式学习

### 1.1 架构缺陷清单（16项）

| # | 类型 | 缺陷描述 | 位置 | 严重度 |
|---|------|---------|------|--------|
| D1 | SRP违反 | ERPAgent 既查询又计算 | erp_agent.py | 🔴 |
| D2 | SRP违反 | ERP_ROUTING_PROMPT 混杂查询+计算+导出指令 | erp_tools.py:258-311 | 🔴 |
| D3 | 数据交接 | 工具间文本传参，LLM 从字符串抠文件路径 | tool_loop_executor.py:560 | 🔴 |
| D4 | 数据交接 | local_stock_query 返回 markdown 文本，无结构化输出 | erp_local_query.py:28-138 | 🔴 |
| D5 | 数据交接 | 所有 local_* 工具返回 markdown，计算时 LLM 要正则解析 | erp_local_query.py 全文件 | 🔴 |
| D6 | 状态管理 | 无 session 文件注册表，每个工具自己宣布文件路径 | tool_executor.py:341 | 🟡 |
| D7 | 元层冗余 | erp_api_search 增加一轮 LLM 推理开销 | api_search.py | 🟡 |
| D8 | 隐式编排 | 导出 Excel = local_data→LLM抠路径→code_execute 三步隐式合约 | erp_unified_query.py + tool_executor.py | 🔴 |
| D9 | 预算风险 | code_execute 超时后计算结果直接丢弃，无检查点 | execution_budget.py + tool_executor.py:379 | 🟡 |
| D10 | 上下文丢失 | context_compressor 替换旧工具结果为 [已归档]，丢失计算输入 | context_compressor.py:147 | 🟡 |
| D11 | 缓存风险 | tool_result_cache 5分钟TTL，跨步查询拿到过期数据 | tool_result_cache.py:47 | 🟡 |
| D12 | 截断丢失 | wrap_for_erp_agent 3000字符截断可能切掉文件路径 | tool_result_envelope.py | 🟡 |
| D13 | 无业务域隔离 | 16个工具扁平共存，无按仓储/采购/财务分组 | erp_tools.py + erp_local_tools.py | 🟡 |
| D14 | 无并行能力 | tool_loop_executor 纯串行，仓储+采购不能同时查 | tool_loop_executor.py | 🟡 |
| D15 | 参数校验通用化 | tool_args_validator 不区分业务域，无按域必填规则 | tool_args_validator.py | 🟡 |
| D16 | 经验记录耦合 | _record_agent_experience 嵌在 ERPAgent 内部 | erp_agent.py:144-182 | 🟡 |
| D17 | 打断恢复缺状态 | loop_snapshot 不保存 SessionFileRegistry，恢复后文件引用丢失 | pending_interaction.loop_snapshot | 🔴 |
| D18 | 打断恢复缺 DAG 进度 | steer 打断没有保存 DAG Round 执行进度 | tool_loop_executor.py:573 | 🔴 |
| D19 | 经验记录无域标识 | knowledge_nodes 的 scope/subcategory 不区分部门 Agent | knowledge_nodes 表 | 🟡 |

---

## 2. 目标架构

```
主Agent（调度层）
  ↓ 调用（tool_call）
ERPAgent（路由层）── 只做意图识别 + 调度部门Agent
  ↓ 结构化调用
┌─────────────┬────────────┬────────────┬───────────┐
│ 仓储Agent    │ 采购Agent   │ 订单Agent   │ 售后Agent  │
│ (warehouse)  │ (purchase)  │ (trade)    │ (aftersale)│
└──────┬──────┴─────┬──────┴─────┬──────┴─────┬─────┘
       │            │            │            │
       ▼            ▼            ▼            ▼
   执行层（UnifiedQueryEngine / ERP API / erp_execute）
                                    ↓ 结构化数据
                              计算Agent（独立）
                              ── code_execute 专用
                              ── 接收结构化输入
                              ── 不做查询
```

### 2.1 各层职责界定

| 层 | 组件 | 做什么 | 不做什么 |
|----|------|-------|---------|
| 路由层 | ERPAgent（瘦身后） | 意图识别、调度部门Agent、汇总结论 | 不直接调查询工具、不做计算 |
| 业务域层 | 部门Agent × 4 | 参数校验、构造查询、理解本域业务语义 | 不跨域、不做计算 |
| 执行层 | UnifiedQueryEngine + ERP API | 接收参数、执行SQL/API、返回**结构化数据** | 不理解业务、不做权限 |
| 计算层 | ComputeAgent | 接收结构化数据、做聚合/对比/导出 | 不做查询、不管数据从哪来 |

---

## 3. 分阶段实施计划

### 3.1 Phase 依赖关系图

```
Phase 0（地基）━━━━━━━━━━━━━━━━━━━━━━┓
  结构化数据协议 + Session文件注册表     ┃
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
          ↓ 依赖
    ┌─────┴──────────────┐
    ↓                    ↓
Phase 1A（轨道A）    Phase 1B（轨道B）     ← 可并行
  ComputeAgent         部门Agent框架
  提取                  + 第一个部门Agent
    ↓                    ↓
    └─────┬──────────────┘
          ↓ 合并
Phase 2（轨道C）
  ERPAgent 瘦身 + 导出流水线原子化
          ↓
    ┌─────┴──────────────┐
    ↓                    ↓
Phase 3A（轨道D）    Phase 3B（轨道E）     ← 可并行
  剩余部门Agent        并行调度能力
  (采购/售后)           tool_loop 改造
    ↓                    ↓
    └─────┬──────────────┘
          ↓
Phase 4（收尾）
  集成测试 + 文档更新
```

### 3.2 并行对话分配

| 对话 | 负责 Phase | 可同时开工时间 |
|------|-----------|-------------|
| 对话 1 | Phase 0（地基）→ Phase 2（合并） | 立即 |
| 对话 2 | Phase 1A（ComputeAgent） | Phase 0 完成后 |
| 对话 3 | Phase 1B（部门Agent框架） | Phase 0 完成后 |
| 对话 4 | Phase 3A（剩余部门Agent） | Phase 1B 完成后 |
| 对话 5 | Phase 3B（并行调度） | Phase 2 完成后 |

---

## 3.5 存储位置调研结论

多 Agent 架构下数据在 Agent 之间传输共有 6 个通道，逐个评估：

| # | 存储位置 | 路径模式 | 改造结论 |
|---|---------|---------|---------|
| 1 | **Staging 目录** | `{ws_root}/org/{org_id}/{user_id}/staging/{conv_id}/` | 🟡 **增强**：文件名加域前缀防冲突 + SessionFileRegistry 注册 |
| 2 | **Output 目录** | `{ws_root}/org/{org_id}/{user_id}/下载/` | 🟢 **不改**：ComputeAgent 写 Excel 仍用此目录 |
| 3 | **Messages 数组** | 内存 list + PostgreSQL `messages` 表 | 🔴 **重新设计**：加 `timestamp` + `[DATA_REF]` 结构化标签 |
| 4 | **pending_interaction** | PostgreSQL `pending_interaction` 表 | 🔴 **必须对齐**：frozen_messages 自动对齐，loop_snapshot 必须加 file_registry + DAG 进度 |
| 5 | **Redis 缓存** | `erp_write_done:{user_id}:{hash}` 等 | 🟢 **不改**：幂等/锁与 Agent 架构无关 |
| 6 | **Knowledge 表** | PostgreSQL `knowledge_nodes` | 🟡 **小改**：subcategory 用和 DATA_REF.source 一致的域标识 |
| 7 | **OSS/CDN** | `https://{cdn}/workspace/...` | 🟢 **不改**：文件上传下载链路不变 |

### pending_interaction 打断恢复对齐设计

打断恢复是多 Agent 架构的关键路径——冻结时必须完整保存状态，恢复时必须精确还原。

**当前 loop_snapshot 保存的**（不够）：
```json
{
    "content_blocks": [...],
    "tool_context_state": ["local_data", "erp_trade_query"],
    "model_id": "gemini-3-pro",
    "budget_snapshot": {"turns_used": 3, "tokens_used": 1200}
}
```

**重构后 loop_snapshot 必须额外保存**：
```json
{
    // ... 原有字段 ...
    
    "file_registry": [
        {
            "key": "warehouse:local_stock_query:1745123456",
            "file_ref": {
                "path": "staging/{conv_id}/warehouse_stock_1745123456.parquet",
                "filename": "warehouse_stock_1745123456.parquet",
                "format": "parquet",
                "row_count": 150,
                "size_bytes": 45000,
                "columns": [
                    {"name": "product_code", "dtype": "text", "label": "商品编码"},
                    {"name": "sellable", "dtype": "integer", "label": "可售库存"}
                ]
            }
        }
    ],
    
    "dag_progress": {
        "total_rounds": 3,
        "completed_rounds": [0, 1],
        "current_round": 2,
        "round_results": {
            "0": [{"source": "aftersale", "summary": "退货数据查询完成，共10条"}],
            "1": [
                {"source": "warehouse", "summary": "库存查询完成，共10条"},
                {"source": "purchase", "summary": "采购查询完成，共8条"}
            ]
        }
    }
}
```

**恢复流程**（thaw 时）：
```python
def _restore_from_pending(self, pending):
    # 1. 原有恢复逻辑（messages + budget + tool_context）
    messages = pending.frozen_messages
    # ...
    
    # 2. 恢复 SessionFileRegistry（新增）
    file_registry = SessionFileRegistry()
    for entry in snapshot.get("file_registry", []):
        ref = FileRef(**entry["file_ref"])
        file_registry._files[entry["key"]] = ref
    
    # 3. 恢复 DAG 进度（新增）
    dag_progress = snapshot.get("dag_progress")
    if dag_progress:
        # 从上次完成的 Round 继续执行
        completed_rounds = dag_progress["completed_rounds"]
        # round_results 里的 ToolOutput 摘要可以重建 context
```

**关键原则**：
- `frozen_messages` 保存的是 messages 数组 → 里面已经有 `timestamp` 和 `[DATA_REF]` → **自动对齐**
- `loop_snapshot` 保存的是执行状态 → 必须**手动加** file_registry 和 dag_progress
- staging 文件在磁盘上（24h清理），冻结期间不会被删 → 恢复后文件还在

### Steer 打断的 DAG 状态处理

用户在 DAG 执行中发新消息（steer），需要区分两种情况：

| 情况 | 处理 |
|------|------|
| **Round 内打断**（某个部门Agent执行中） | 当前 Agent 的结果标记 partial，已完成的 Round 结果保留，注入用户消息让 ERPAgent 重新规划 |
| **Round 间打断**（Round 0 完成，Round 1 未开始） | Round 0 结果保留，注入用户消息，ERPAgent 可以修改后续 Round 的计划 |

```python
# ERPAgent DAG 执行中的 steer 检查
for i, round in enumerate(plan.rounds):
    # 执行当前 Round...
    results = await asyncio.gather(*tasks)
    round_results[i] = results
    
    # ── DAG 级打断检查 ──
    steer_msg = ws_manager.check_steer(task_id)
    if steer_msg:
        # 保留已完成 Round 的结果
        # 注入用户新消息
        # ERPAgent 用 LLM 决定：继续/修改/放弃后续 Round
        break
```

### Messages 共享摘要通道——两层设计

Messages 数组是所有 Agent 之间的共享摘要通道。设计分两层：

**第一层：通道层（固定字段，每条消息都有）**

```python
messages.append({
    "role": "tool",
    "tool_call_id": tc["id"],
    "timestamp": "2026-04-16T14:32:05+08:00",   # ← 写入时间戳（必填）
    "content": "...",                             # ← 正文（含 DATA_REF 标签或纯文本）
})
```

`timestamp` 是通道层的事，和业务内容无关，用途：
- 主 Agent 判断数据新鲜度（"仓储结果是 2 分钟前的，还能用"）
- 多 Round 编排时排序和超时检测
- 对比哪个 Agent 先返回

**第二层：内容层（DATA_REF 标签，按任务动态决定）**

标签里的字段不是写死的模板，是 Agent 根据任务返回的内容**自己判断该带什么**：

```
# ── 固定字段（有 DATA_REF 就必须带）──
source          # 哪个 Agent 产出的
storage         # inline | file
rows            # 多少行
columns         # 列名 + 类型 + 中文标签

# ── 动态字段（Agent 自己判断要不要带）──
doc_type        # 查的是订单？采购单？售后单？—— 有就带，查仓库列表就没有
time_range      # 数据的业务时间范围 —— 有时间过滤就带，查库存快照就不带
time_column     # 用的哪个时间字段 —— 有就带
path            # staging 文件路径 —— file 模式才有
format          # parquet / csv —— file 模式才有
size_kb         # 文件大小 —— file 模式才有
data            # 内联 JSON —— inline 模式才有
preview         # 前3行预览 —— file 模式才有
filters         # 用了什么过滤条件 —— Agent 觉得有用就带
```

### 示例1：大数据走文件（售后退货查询）

Agent 判断：有 doc_type、有 time_range、有 time_column → 都带上

```
售后退货数据查询完成，共 2,341 条记录。

[DATA_REF]
source: aftersale_agent
storage: file
rows: 2341
path: staging/{conv_id}/aftersale_returns_1745123458.parquet
format: parquet
size_kb: 856
doc_type: aftersale
time_range: 2026-03-01 ~ 2026-03-31
time_column: doc_created_at
columns:
  - product_code: text     # 商品编码
  - product_name: text     # 商品名称
  - platform: text         # 平台
  - return_qty: integer    # 退货数量
  - return_amount: numeric # 退款金额
  - shop_name: text        # 店铺名称
preview:
  {"product_code":"A001","product_name":"防晒霜SPF50","return_qty":15,...}
  {"product_code":"B003","product_name":"洗面奶200ml","return_qty":8,...}
[/DATA_REF]
```

### 示例2：小数据内联（库存查询）

Agent 判断：没有 time_range（库存是快照）、没有 doc_type → 不带这些字段

```
库存查询完成，共 3 条记录。

[DATA_REF]
source: warehouse_agent
storage: inline
rows: 3
columns:
  - product_code: text   # 商品编码
  - warehouse: text      # 仓库
  - sellable: integer    # 可售
  - onway: integer       # 在途
data:
  [{"product_code":"A001","warehouse":"北京","sellable":30,"onway":50},
   {"product_code":"A001","warehouse":"上海","sellable":15,"onway":0}]
[/DATA_REF]
```

### 示例3：纯文本回复（仓库列表查询）

Agent 判断：就是个列表，不需要 DATA_REF → 直接返回纯文本

```
共 8 个仓库（实体5，虚拟3）：
  1. 北京中心仓（实体，启用）
  2. 上海前置仓（实体，启用）
  ...
```

### 设计原则

| 原则 | 说明 |
|------|------|
| **通道层强制** | `timestamp` 每条消息必带，主 Agent 用来编排和判断新鲜度 |
| **标签层最小必填** | 有 `[DATA_REF]` 时 `source` / `storage` / `rows` / `columns` 必填 |
| **动态字段不强制** | `doc_type` / `time_range` / `path` 等由 Agent 根据任务自行判断 |
| **无结构化数据时不带标签** | 纯列表、纯文本回复直接塞 content，不加 `[DATA_REF]` |

### 自动分流规则

| 条件 | 存储方式 | messages 标签 |
|------|---------|-------------|
| ≤200 行 | `storage: inline`，data 字段放 JSON | ComputeAgent 直接用，不读文件 |
| >200 行 | `storage: file`，写 staging parquet | ComputeAgent 读文件，但列名已在标签里 |
| 无结构化数据 | 无 `[DATA_REF]` 标签 | 直接塞 messages，和现在一样 |

### Staging 文件命名规范（防并行冲突）

```
staging/{conv_id}/
  ├── warehouse_stock_1745123456.parquet       ← 仓储Agent
  ├── purchase_orders_1745123457.parquet       ← 采购Agent
  ├── aftersale_returns_1745123458.parquet     ← 售后Agent
  ├── trade_orders_1745123459.parquet          ← 订单Agent
  └── compute_merged_1745123460.parquet        ← 计算Agent中间结果
```

命名规则：`{agent_domain}_{业务描述}_{unix_timestamp}.parquet`

---

## 4. Phase 0：地基——结构化数据协议 + Session文件注册表

**解决缺陷**：D3, D4, D5, D6, D8, D12

### 4.1 ToolOutput 结构化协议

**新建文件**：`backend/services/agent/tool_output.py`

```python
"""
结构化工具输出协议。
所有工具返回 ToolOutput，不再返回裸字符串。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OutputFormat(Enum):
    """工具输出格式类型"""
    TEXT = "text"           # 纯文本（给LLM阅读）
    TABLE = "table"         # 结构化表格（≤200行内联 JSON）
    FILE_REF = "file_ref"   # 大数据走文件（>200行写 staging）


class OutputStatus(Enum):
    """执行状态"""
    OK = "ok"              # 查询成功，有数据
    EMPTY = "empty"        # 查询成功，确实没数据（业务合理）
    PARTIAL = "partial"    # 查询成功但不完整（超时截断）
    ERROR = "error"        # 查询失败（异常/权限/接口错误）


@dataclass(frozen=True)
class ColumnMeta:
    """列元信息 — 让下游 Agent 知道每列叫什么、是什么类型"""
    name: str               # 列名（英文，和 parquet/JSON key 一致）
    dtype: str              # text / integer / numeric / timestamp / boolean
    label: str = ""         # 中文标签（给 LLM 看）
    # 示例：ColumnMeta("return_qty", "integer", "退货数量")


@dataclass(frozen=True)
class FileRef:
    """Staging 文件引用 — 大数据的结构化传输凭证"""
    path: str                   # 相对路径 staging/{conv_id}/{filename}
    filename: str               # 文件名（带域标识，如 warehouse_stock_xxx.parquet）
    format: str                 # parquet / csv / xlsx
    row_count: int              # 行数
    size_bytes: int             # 字节数
    columns: list[ColumnMeta]   # 完整列元信息（名称+类型+中文标签）
    preview: str = ""           # 前3行预览文本


@dataclass
class ToolOutput:
    """
    统一工具输出 — 所有 Agent/工具的标准返回格式。
    
    ┌─────────────────────────────────────────────────┐
    │ 协议层字段（ToolOutput 固定的，和业务无关）：       │
    │   summary  — 文本摘要（必填）                     │
    │   format   — TEXT / TABLE / FILE_REF             │
    │   source   — 哪个 Agent 产出的（必填）            │
    │   columns  — 列名+类型+标签（TABLE/FILE_REF 必填）│
    │   data     — 内联数据（TABLE 模式）               │
    │   file_ref — 文件引用（FILE_REF 模式）            │
    │                                                  │
    │ 业务层字段（Agent 自己判断该放什么，全走 metadata）：│
    │   metadata["doc_type"]    — 有就放，没有就不放     │
    │   metadata["time_range"]  — 有就放，没有就不放     │
    │   metadata["time_column"] — 有就放，没有就不放     │
    │   metadata["platform"]    — 有就放                │
    │   metadata["filters"]     — 有就放                │
    │   metadata[任何业务字段]    — Agent 自主决定        │
    └─────────────────────────────────────────────────┘
    
    设计原则：
    - 协议层字段是「通道」的事，所有 Agent 共用，不能随便加
    - 业务层字段是「内容」的事，Agent 根据任务自主决定带什么
    - ToolOutput 不硬编码任何业务概念（doc_type/time_range 等）
    """
    # ── 协议层（固定）──
    summary: str                                   # 文本摘要（必填）
    format: OutputFormat = OutputFormat.TEXT        # 输出格式
    source: str = ""                               # 来源 Agent 域标识（必填）
    status: OutputStatus = OutputStatus.OK         # 执行状态（OK/EMPTY/PARTIAL/ERROR）
    error_message: str = ""                        # 错误信息（ERROR 时必填）
    columns: list[ColumnMeta] | None = None        # 列元信息（TABLE/FILE_REF 必填）
    data: list[dict[str, Any]] | None = None       # 内联数据（TABLE 模式）
    file_ref: FileRef | None = None                # 文件引用（FILE_REF 模式）
    # ── 业务层（动态，Agent 自主决定）──
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_message_content(self) -> str:
        """
        转为 messages 数组里的 content 字符串。
        
        注意：这只生成 content 字段。
        timestamp 由通道层（ToolLoopExecutor）在 append 时统一注入，
        不是 ToolOutput 的职责。
        
        DATA_REF 标签的动态字段由 Agent 根据任务结果自行填充，
        不强制所有字段都出现。
        """
        # 纯文本：不加标签
        if self.format == OutputFormat.TEXT:
            return self.summary
        
        parts = [self.summary]
        tag_lines = ["\n[DATA_REF]"]
        
        # ── 最小必填字段 ──
        tag_lines.append(f"source: {self.source}")
        if self.file_ref:
            tag_lines.append("storage: file")
            tag_lines.append(f"rows: {self.file_ref.row_count}")
            tag_lines.append(f"path: {self.file_ref.path}")
            tag_lines.append(f"format: {self.file_ref.format}")
            tag_lines.append(f"size_kb: {self.file_ref.size_bytes // 1024}")
        elif self.data is not None:
            tag_lines.append("storage: inline")
            tag_lines.append(f"rows: {len(self.data)}")
        
        # ── 动态字段（全走 metadata，Agent 自己决定放什么）──
        for key, val in self.metadata.items():
            if val is not None and val != "":
                tag_lines.append(f"{key}: {val}")
        
        # ── 列信息（必填：有 DATA_REF 就必须有 columns）──
        cols = self.columns or (self.file_ref.columns if self.file_ref else None)
        if cols:
            tag_lines.append("columns:")
            for col in cols:
                label_part = f"  # {col.label}" if col.label else ""
                tag_lines.append(f"  - {col.name}: {col.dtype}{label_part}")
        
        # ── 内联数据 or 文件预览 ──
        if self.data is not None and len(self.data) <= 200:
            import json
            tag_lines.append("data:")
            tag_lines.append(f"  {json.dumps(self.data, ensure_ascii=False)}")
        elif self.file_ref and self.file_ref.preview:
            tag_lines.append(f"preview:\n  {self.file_ref.preview}")
        
        tag_lines.append("[/DATA_REF]")
        parts.append("\n".join(tag_lines))
        return "\n".join(parts)

    def to_compute_input(self) -> dict:
        """转为 ComputeAgent 的结构化输入（Python dict，不是文本）"""
        result: dict[str, Any] = {
            "source": self.source,
            "summary": self.summary,
        }
        
        # 业务字段：原样透传（Agent 放了什么就传什么）
        if self.metadata:
            result["metadata"] = self.metadata
        
        # 列元信息
        cols = self.columns or (self.file_ref.columns if self.file_ref else None)
        if cols:
            result["columns"] = [
                {"name": c.name, "dtype": c.dtype, "label": c.label}
                for c in cols
            ]
        
        # 数据：内联 JSON 或文件路径
        if self.data is not None:
            result["data"] = self.data
        if self.file_ref:
            result["file_ref"] = {
                "path": self.file_ref.path,
                "filename": self.file_ref.filename,
                "format": self.file_ref.format,
                "row_count": self.file_ref.row_count,
            }
        return result
```

### 4.2 SessionFileRegistry

**新建文件**：`backend/services/agent/session_file_registry.py`

```python
"""
Session 级文件注册表。
跟踪当前会话中所有工具写入的 staging 文件，
供 ComputeAgent 按名查找，不再依赖 LLM 从文本抠路径。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .tool_output import FileRef


@dataclass
class SessionFileRegistry:
    """
    会话级文件注册表。
    
    key = "{domain}:{tool_name}:{timestamp}"
    防止多个部门 Agent 都调 local_data 时互相覆盖。
    例：warehouse:local_data:1745123456, purchase:local_data:1745123457
    """
    _files: dict[str, FileRef] = field(default_factory=dict)

    def register(self, domain: str, tool_name: str, file_ref: FileRef) -> None:
        """注册文件（key = domain:tool_name:timestamp，不会覆盖）"""
        import time
        key = f"{domain}:{tool_name}:{int(time.time())}"
        self._files[key] = file_ref

    def get_by_domain(self, domain: str) -> list[FileRef]:
        """按域查文件（一个域可能有多个文件）"""
        return [ref for key, ref in self._files.items() if key.startswith(f"{domain}:")]

    def get_latest(self) -> FileRef | None:
        """获取最新注册的文件"""
        if not self._files:
            return None
        return list(self._files.values())[-1]

    def list_all(self) -> list[tuple[str, FileRef]]:
        """列出所有文件"""
        return list(self._files.items())

    def to_prompt_text(self) -> str:
        """生成文件清单文本（注入 ComputeAgent prompt）"""
        if not self._files:
            return "当前会话无暂存文件。"
        lines = ["当前会话暂存文件："]
        for key, ref in self._files.items():
            domain = key.split(":")[0]
            col_names = [c.name for c in ref.columns[:8]] if ref.columns else []
            lines.append(
                f"  - {ref.filename}（来自 {domain}，"
                f"{ref.row_count}行，列: {', '.join(col_names)}）"
            )
        return "\n".join(lines)
```

### 4.3 所有执行层函数一次性改为返回 ToolOutput

**策略**：不搞 `_raw` + 原函数两个版本。所有函数直接改返回类型为 ToolOutput，一步到位。
- 部门 Agent 读 `.data` 拿原始数据
- ToolLoopExecutor 调 `.to_message_content()` 拿文本给 LLM
- 一个函数、一个返回类型、两种用法

**改造前**（以 local_stock_query 为例）：
```python
async def local_stock_query(db, product_code, ...) -> str:
    rows = db.table("erp_stock_status").select("*")...
    lines = ["商品 A001 库存：..."]     # 拼 markdown
    return "\n".join(lines)             # ← str
```

**改造后**（直接返回 ToolOutput）：
```python
async def local_stock_query(db, product_code, ...) -> ToolOutput:
    rows = db.table("erp_stock_status").select("*")...
    return ToolOutput(
        summary=f"商品 {product_code} 库存：可售{total}件",
        format=OutputFormat.TABLE,
        source="warehouse",
        columns=[
            ColumnMeta("sku", "text", "SKU编码"),
            ColumnMeta("sellable_num", "integer", "可售库存"),
            ColumnMeta("total_num", "integer", "总库存"),
            ColumnMeta("onway_num", "integer", "采购在途"),
        ],
        data=rows,
        metadata={"product_code": product_code},
    )

# 调用方1（部门Agent）：
result = await local_stock_query(db, "A001")
rows = result.data          # → list[dict]，直接用

# 调用方2（ToolLoopExecutor，给LLM看）：
content = result.to_message_content()  # → str（摘要 + DATA_REF 标签）
```

**需改造的工具清单（一次性全改，无过渡期）**：

| 文件 | 函数 | 改造目标 | 行数估算 |
|------|------|---------|---------|
| erp_local_query.py | `local_stock_query` | → ToolOutput(TABLE) | ~40行 |
| erp_local_query.py | `local_platform_map_query` | → ToolOutput(TABLE) | ~30行 |
| erp_local_query.py | `local_shop_list` | → ToolOutput(TEXT)（纯列表） | ~20行 |
| erp_local_query.py | `local_warehouse_list` | → ToolOutput(TEXT)（纯列表） | ~20行 |
| erp_local_identify.py | `local_product_identify` | → ToolOutput(TABLE) | ~30行 |
| erp_local_compare_stats.py | `local_compare_stats` | → ToolOutput(TABLE) | ~30行 |
| erp_stats_query.py | `local_product_stats` | → ToolOutput(TABLE) | ~30行 |
| erp_unified_query.py | `_summary()` | → ToolOutput(TABLE) | ~30行 |
| erp_unified_query.py | `_detail()` | → ToolOutput(TABLE) | ~30行 |
| erp_unified_query.py | `_export()` | → ToolOutput(FILE_REF) | ~20行 |
| dispatcher.py | `execute()` | → ToolOutput(TABLE) | ~30行 |
| tool_executor.py | `_fetch_all_pages()` | → ToolOutput(FILE_REF) | ~20行 |
| **测试文件** | **~11个文件、~120处断言** | 从 `assert "x" in result` 改为 `assert result.data[0]["x"] == y` 或 `assert "x" in result.summary` | ~120处 |

### 4.4 ToolLoopExecutor 适配（通道层 + 内容层分离）

**修改文件**：`tool_loop_executor.py`

```python
from datetime import datetime, timezone

# 改造前（line 560）：
messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

# 改造后（一次性，所有工具都返回 ToolOutput，不保留 str 兼容）：
now_iso = datetime.now(timezone.utc).isoformat()

messages.append({
    "role": "tool",
    "tool_call_id": tc["id"],
    "timestamp": now_iso,                          # 通道层：写入时间
    "content": result.to_message_content(),         # 内容层：摘要 + DATA_REF
})

# 文件注册到 SessionFileRegistry
if result.file_ref:
    self._file_registry.register(result.source, tool_name, result.file_ref)
```

**关键设计**：
- 所有工具统一返回 ToolOutput，无 isinstance 分支
- `timestamp` 由 ToolLoopExecutor 统一注入（通道层职责）
- `content` 里的 `[DATA_REF]` 动态字段由各 Agent/工具自己决定带什么（内容层职责）
- 主 Agent 编排时读 `timestamp` 判断新鲜度和排序
- ScheduledTaskAgent 也用 ToolLoopExecutor，同步受益

### 4.5 Phase 0 交付物

| 交付物 | 文件 | 状态 |
|--------|------|------|
| ToolOutput 协议 | `services/agent/tool_output.py` | 新建 |
| SessionFileRegistry | `services/agent/session_file_registry.py` | 新建 |
| local_* 返回 ToolOutput | `erp_local_query.py` | 改造 |
| UnifiedQueryEngine 返回 ToolOutput | `erp_unified_query.py` | 改造 |
| fetch_all_pages 返回 ToolOutput | `tool_executor.py` | 改造 |
| ToolLoopExecutor 适配 | `tool_loop_executor.py` | 改造 |
| tool_result_envelope 适配 | `tool_result_envelope.py` | 改造 |
| 单元测试 | `tests/test_tool_output.py` | 新建 |

---

## 5. Phase 1A：ComputeAgent 提取（轨道A）

**解决缺陷**：D1, D2, D8, D9

### 5.1 ComputeAgent 设计

**新建文件**：`backend/services/agent/compute_agent.py`

```python
"""
独立计算Agent。
职责：接收结构化数据输入，执行聚合/对比/导出，返回结论。
不做查询、不选工具、不管数据从哪来。
"""

class ComputeAgent:
    """
    计算Agent — 单一职责：数据计算与导出。
    
    不需要 db（不查数据库）、不需要 org_id（不做权限）。
    只需要：staging 目录（读写文件）+ 文件注册表（知道有哪些数据）+ 时间上下文。
    """

    def __init__(self, staging_dir: str, file_registry: SessionFileRegistry, request_ctx):
        self._staging_dir = staging_dir        # 读写 parquet/xlsx 的目录
        self._file_registry = file_registry    # 知道当前会话有哪些数据文件
        self._request_ctx = request_ctx        # 时间上下文（计算"最近7天"等需要）

    async def execute(self, task: ComputeTask) -> ComputeResult:
        """
        执行计算任务。
        
        task 包含：
        - instruction: 计算指令（自然语言）
        - inputs: list[ToolOutput] — 结构化输入数据
        - output_format: "text" | "excel" | "chart"
        """
        # 1. 构建 system prompt（只含计算指令，不含查询规则）
        # 2. 注入 file_registry 清单（文件路径+列名）
        # 3. 注入 inputs 的结构化摘要
        # 4. 调用 code_execute（唯一工具）
        # 5. 返回 ComputeResult
```

### 5.2 ComputeAgent 的 system prompt

```
你是计算专家。你的唯一职责是对已获取的数据进行计算、统计、对比和导出。

## 你能做的
- 读取 staging 文件（路径已提供，不需要猜）
- pandas 聚合、分组、透视
- 时间差计算、环比同比
- 导出 Excel / CSV
- 生成统计结论

## 你不能做的
- 查询数据（数据已由其他 Agent 获取）
- 调用 ERP API
- 修改数据

## 当前可用数据
{file_registry.to_prompt_text()}

## 输入数据
{inputs 的结构化摘要}
```

### 5.3 ERPAgent 调用 ComputeAgent 的流程

```
ERPAgent 工具循环中发现需要计算：
  ↓
1. 收集已有的 ToolOutput（从 file_registry 获取）
2. 构建 ComputeTask（指令 + 输入 + 输出格式）
3. 调用 compute_agent.execute(task)
4. 获取 ComputeResult
5. 拼入最终回复
```

### 5.4 关键变更

| 文件 | 变更内容 |
|------|---------|
| `compute_agent.py` | 新建：独立计算Agent |
| `compute_types.py` | 新建：ComputeTask / ComputeResult 数据类 |
| `erp_agent.py` | 改造：移除 code_execute 直接调用，改为调 ComputeAgent |
| `erp_tools.py` | 改造：ERP_ROUTING_PROMPT 删除计算相关指令 |
| `tool_domains.py` | 改造：新增 COMPUTE 域，code_execute 从 SHARED 移到 COMPUTE |
| `config/compute_tools.py` | 新建：计算Agent工具定义（只有 code_execute） |

---

## 6. Phase 1B：部门Agent框架 + 第一个部门Agent（轨道B）

**解决缺陷**：D13, D15

### 6.1 DepartmentAgent 基类

**新建文件**：`backend/services/agent/department_agent.py`

```python
"""
部门Agent基类。
每个部门Agent只管自己的业务域，理解本域语义，校验本域参数。
"""
from abc import ABC, abstractmethod


class DepartmentAgent(ABC):
    """部门Agent基类"""

    @property
    @abstractmethod
    def domain(self) -> str:
        """业务域标识：warehouse / purchase / trade / aftersale"""

    @property
    @abstractmethod
    def tools(self) -> list[str]:
        """本域可用工具列表"""

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """本域专用 system prompt"""

    @abstractmethod
    def validate_params(self, action: str, params: dict) -> ValidationResult:
        """
        本域参数校验。
        返回 ValidationResult：
        - ok: 参数齐全，可执行
        - missing: 缺少必填参数，返回协商提示
        - conflict: 参数互斥，返回冲突说明
        """

    async def execute(self, query: str, parent_outputs: list[ToolOutput]) -> ToolOutput:
        """
        执行本域查询。
        1. 参数校验（不足则返回协商提示）
        2. 调用执行层工具
        3. 返回 ToolOutput（结构化）
        """
```

### 6.2 第一个部门Agent：仓储Agent

**选择仓储Agent作为第一个**的理由：
- 工具边界最清晰（local_stock_query、local_warehouse_list）
- 用户反馈的缺货查询问题直接相关
- 不涉及写操作，风险低

**新建文件**：`backend/services/agent/departments/warehouse_agent.py`

```python
class WarehouseAgent(DepartmentAgent):
    domain = "warehouse"
    tools = [
        "local_stock_query",       # 库存查询
        "local_warehouse_list",    # 仓库列表
        "local_data",              # doc_type=receipt/shelf 的统一查询
    ]

    def validate_params(self, action, params):
        if action == "stock_query":
            if not params.get("product_code") and not params.get("keyword"):
                return ValidationResult.missing(["商品编码或关键词"])
        elif action == "shortage_query":
            if not params.get("platform"):
                return ValidationResult.missing(["平台"])
            if not params.get("time_range"):
                return ValidationResult.missing(["时间范围"])
        return ValidationResult.ok()

    @property
    def system_prompt(self):
        return """你是仓储专家Agent。你负责：
        - 库存查询（可售/锁定/在途）
        - 缺货分析（哪些SKU缺货、缺多少）
        - 仓库信息查询
        - 出入库记录查询（入库单/上架单）
        
        你不负责：采购、订单、售后、财务
        
        参数规则：
        - 缺货查询必须指定：平台 + 时间范围
        - 库存查询必须指定：商品编码 或 关键词
        - 时间范围不能超过90天
        """
```

### 6.3 Phase 1B 交付物

| 交付物 | 文件 | 状态 |
|--------|------|------|
| DepartmentAgent 基类 | `services/agent/department_agent.py` | 新建 |
| ValidationResult 类型 | `services/agent/department_types.py` | 新建 |
| WarehouseAgent | `services/agent/departments/warehouse_agent.py` | 新建 |
| 仓储域工具定义 | `config/warehouse_tools.py` | 新建 |
| 仓储域参数校验规则 | 内嵌于 WarehouseAgent | — |
| 单元测试 | `tests/test_warehouse_agent.py` | 新建 |

---

## 7. Phase 2：ERPAgent 瘦身 + 导出流水线原子化

**解决缺陷**：D1, D2, D7, D8, D16

### 7.1 ERPAgent 瘦身

**改造目标**：ERPAgent 从"干所有事"变为"只做路由调度"

**改造前职责（6项）**：
```
ERPAgent = 意图识别 + 工具选择 + 查询执行 + 计算编排 + 文件管理 + 经验记录
```

**改造后职责（2项）**：
```
ERPAgent = 意图识别 + 调度部门Agent/ComputeAgent
```

**具体变更**：

| 从 ERPAgent 移出 | 移到哪里 |
|-----------------|---------|
| code_execute 调用 | ComputeAgent（Phase 1A） |
| 查询工具直接调用 | 部门Agent（Phase 1B） |
| 参数校验 | 部门Agent.validate_params() |
| staging 文件追踪 | SessionFileRegistry（Phase 0） |
| 经验记录 | 独立 ExperienceRecorder 类 |
| ERP_ROUTING_PROMPT 计算部分 | ComputeAgent.system_prompt |

### 7.2 导出流水线原子化

**改造前**：3步隐式编排（职责混乱）
```
local_data(export) → LLM抠文件路径 → code_execute(写Excel)
```

**改造后**：执行层只管查数据，格式转换交给 ComputeAgent

```
方式A（简单导出，不需要计算）：
  部门Agent → 执行层查数据 → ToolOutput(FILE_REF, parquet)
    → ComputeAgent.execute(instruction="转为Excel", output_format="xlsx")
    → ComputeAgent 用 code_execute 做格式转换（它知道列名，不用猜）

方式B（带计算的导出）：
  多个部门Agent → 各自返回 ToolOutput
    → ComputeAgent.execute(instruction="合并计算+导出Excel", inputs=[...])
    → ComputeAgent 做计算+格式转换（一步完成）
```

**职责划分**：
| 组件 | 做什么 | 不做什么 |
|------|-------|---------|
| 执行层（UnifiedQueryEngine） | 查数据 → 返回 parquet | 不做格式转换（xlsx/csv） |
| ComputeAgent | 格式转换 + 计算 | 不查数据 |

**为什么不在执行层直接输出 xlsx**：
- 执行层的职责是"接参数、执行SQL、返回数据"，格式转换（加表头、加样式、列宽调整）是另一件事
- 如果执行层输出 xlsx，那 ComputeAgent 要改动已有 xlsx 就很麻烦（pandas 读 xlsx 比 parquet 慢10倍）
- 统一走 parquet → ComputeAgent 做最终格式转换，链路干净

### 7.3 erp_api_search 改造

从"LLM调用发现工具→再调工具"改为：
- 部门Agent 启动时预加载本域所有 action 文档
- 删除 erp_api_search 作为独立工具
- action 文档内嵌到部门Agent 的 system prompt

---

## 8. Phase 3A：剩余部门Agent（轨道D）

### 8.1 采购Agent

**新建文件**：`backend/services/agent/departments/purchase_agent.py`

```python
class PurchaseAgent(DepartmentAgent):
    domain = "purchase"
    tools = [
        "local_data",              # doc_type=purchase/purchase_return
        "erp_purchase_query",      # 远程采购API
    ]

    def validate_params(self, action, params):
        if action == "arrival_progress":
            # 到货进度查询：必须有 SKU 或采购单号
            if not params.get("sku_list") and not params.get("po_no"):
                return ValidationResult.missing(["SKU列表 或 采购单号"])
        elif action == "supplier_query":
            if not params.get("supplier_name") and not params.get("supplier_id"):
                return ValidationResult.missing(["供应商名称 或 ID"])
        return ValidationResult.ok()
```

### 8.2 订单Agent

```python
class TradeAgent(DepartmentAgent):
    domain = "trade"
    tools = [
        "local_data",              # doc_type=order
        "erp_trade_query",         # 远程订单API
        "erp_taobao_query",        # 淘宝奇门API
    ]
```

### 8.3 售后Agent

```python
class AftersaleAgent(DepartmentAgent):
    domain = "aftersale"
    tools = [
        "local_data",              # doc_type=aftersale
        "erp_aftersales_query",    # 远程售后API
    ]
```

---

## 9. Phase 3B：DAG编排引擎 + 并行调度（轨道E）

**解决缺陷**：D14

> **核心目标**：ERPAgent 作为路由层，能处理任意 N 个部门的组合查询 + 计算，
> 不管是2个部门还是5个部门，不管是全并行还是有依赖的串并行混合。
> 一劳永逸，不再为每种场景写特殊逻辑。

### 9.1 三种编排模式

| 模式 | 适用场景 | 示例 |
|------|---------|------|
| **单域直通** | 只涉及一个部门 | "查一下A001库存" |
| **多域并行** | 多个部门互不依赖 | "缺货数量 + 采购到货进度" |
| **DAG串并行** | 前一步的结果是后一步的输入 | "退货率最高的商品→查库存→查采购→汇总导出" |

### 9.2 ERPAgent 路由层（DAG编排器）

```python
class ERPAgent:
    """
    瘦身后的 ERPAgent = 纯路由层。
    职责：分析意图 → 构建执行计划（DAG）→ 按依赖关系调度部门Agent → 汇总。
    """
    
    async def execute(self, query: str, parent_messages: list) -> ERPAgentResult:
        # ── Step 1: 意图分析 → 生成执行计划 ──
        plan = await self._plan_execution(query)
        # plan 是一个 DAG:
        # [
        #   Round(agents=[AftersaleAgent], depends_on=[]),           # Round 0
        #   Round(agents=[WarehouseAgent, PurchaseAgent], depends_on=[0]),  # Round 1（并行，依赖Round0）
        #   Round(agents=[ComputeAgent], depends_on=[0, 1]),          # Round 2（依赖全部）
        # ]
        
        # ── Step 2: 按 Round 顺序执行，Round 内并行 ──
        round_results: dict[int, list[ToolOutput]] = {}
        
        for i, round in enumerate(plan.rounds):
            # 收集前序依赖的输出作为 context
            context = []
            for dep_idx in round.depends_on:
                context.extend(round_results[dep_idx])
            
            # Round 内多个 Agent 并行执行
            if len(round.agents) == 1:
                result = await round.agents[0].execute(query, context=context)
                round_results[i] = [result]
            else:
                tasks = [
                    agent.execute(query, context=context) 
                    for agent in round.agents
                ]
                results = await asyncio.gather(*tasks)
                round_results[i] = list(results)
        
        # ── Step 3: 汇总所有 Round 的结果 ──
        all_outputs = [out for outs in round_results.values() for out in outs]
        return self._build_final_result(all_outputs)
    
    async def _plan_execution(self, query: str) -> ExecutionPlan:
        """
        用 LLM 分析用户意图，生成执行计划。
        
        LLM 返回结构化 JSON:
        {
          "rounds": [
            {"agents": ["aftersale"], "task": "查退货数据", "depends_on": []},
            {"agents": ["warehouse", "purchase"], "task": "查库存和采购", "depends_on": [0]},
            {"agents": ["compute"], "task": "合并对比导出", "depends_on": [0, 1]}
          ]
        }
        """
```

### 9.3 跨2域场景：缺货 + 采购到货

```
用户："查缺货数量和采购单到货进度能不能赶上"

_plan_execution →
  Round 0: [WarehouseAgent, PurchaseAgent]  ← 无依赖，直接并行
  Round 1: [ComputeAgent]                   ← 依赖 Round 0

执行：
  Round 0（并行）：
    WarehouseAgent → ToolOutput(data=[{sku:"A", shortage:50, deadline:"04-20"}, ...])
    PurchaseAgent  → ToolOutput(data=[{sku:"A", eta:"04-18", po_no:"PO-001"}, ...])
  
  Round 1：
    ComputeAgent(inputs=[warehouse_result, purchase_result])
    → "共5个缺货SKU，3个能赶上，2个（A003/A007）将超时2-3天"
```

### 9.4 跨3域场景：退货率 + 库存 + 采购补货

```
用户："上个月退货率最高的10个商品，查库存够不够卖，采购有没有在补货，导出Excel"

_plan_execution →
  Round 0: [AftersaleAgent]                       ← 先查退货（确定目标商品）
  Round 1: [WarehouseAgent, PurchaseAgent]         ← 用 Round 0 的商品列表，并行查
  Round 2: [ComputeAgent]                          ← 三份数据合并计算导出

执行：
  Round 0（串行）：
    AftersaleAgent("上个月各平台退货数据，按商品分组取TOP10")
      → ToolOutput(data=[
          {product_code:"A001", platform:"tb",  return_qty:150, return_rate:0.12},
          {product_code:"B003", platform:"pdd", return_qty:89,  return_rate:0.08},
          ...共10条
        ])

  Round 1（并行，context = Round 0 的商品列表）：
    WarehouseAgent("查这些商品库存", context=round0_result)
      → ToolOutput(data=[
          {product_code:"A001", sellable:30, daily_sales:25, days_cover:1.2},
          ...
        ])
    PurchaseAgent("查这些商品采购在途", context=round0_result)
      → ToolOutput(data=[
          {product_code:"A001", po_no:"PO-2026-089", eta:"04-22", onway_qty:500},
          ...
        ])

  Round 2（计算，inputs = 3份结构化数据）：
    ComputeAgent.execute(
      instruction="合并三份数据，计算库存可售天数、采购能否赶上、导出Excel",
      inputs=[aftersale_result, warehouse_result, purchase_result],
      output_format="xlsx"
    )
    → ComputeAgent prompt 自动注入：
      "输入1(售后): 10行, 列=[product_code, platform, return_qty, return_rate]"
      "输入2(仓储): 10行, 列=[product_code, sellable, daily_sales, days_cover]"
      "输入3(采购): 10行, 列=[product_code, po_no, eta, onway_qty]"
    → code_execute: pd.merge(df1, df2, on="product_code").merge(df3, on="product_code")
    → 输出 Excel + 文字结论
```

### 9.5 跨N域的通用保证

| 特性 | 保证 |
|------|------|
| **部门数量** | 不限。Round 内可放任意数量的 Agent，自动并行 |
| **依赖关系** | DAG 表达。Round 之间可声明 depends_on，有依赖串行，无依赖并行 |
| **数据传递** | 结构化。每个 Agent 返回 ToolOutput(data=list[dict])，下游 Agent 通过 context 参数接收 |
| **大数据量** | 自动走 FILE_REF。数据量大时 Agent 返回 ToolOutput(file_ref=FileRef)，ComputeAgent 从 staging 读 parquet |
| **小数据量** | 内联传递。数据量小时 Agent 返回 ToolOutput(data=[...])，ComputeAgent 直接用 JSON |
| **列名已知** | ComputeAgent prompt 自动注入列名清单，code_execute 不用猜字段名 |
| **超时保护** | 每个 Agent 独立预算。某个 Agent 超时不影响其他 Agent 的结果 |
| **参数不足** | 部门 Agent 先校验。缺参数直接返回协商提示，不浪费后续 Round |
| **新增部门** | 只加一个 DepartmentAgent 子类。ERPAgent 路由层和 ComputeAgent 不需要改 |

### 9.6 数据量自动分流（小数据内联 vs 大数据走文件）

```python
# DepartmentAgent 基类内置的返回策略
class DepartmentAgent(ABC):
    
    INLINE_THRESHOLD = 200  # ≤200行：直接放 data 字段（JSON传递）
    FIELD_MAP: dict[str, str] = {}   # 子类覆盖：底层字段名 → 标准字段名
    allowed_doc_types: list[str] = []  # 子类覆盖：允许查询的 doc_type
    
    def _build_output(
        self,
        rows: list[dict],
        summary: str,
        columns: list[ColumnMeta],       # 必填：列元信息
        **business_fields,               # 动态：Agent 自己决定带什么业务字段
    ) -> ToolOutput:
        """
        构建 ToolOutput。
        - source 从 self.domain 自动取（协议层必填）
        - columns 必须传（协议层必填）
        - business_fields 全部放 metadata（业务层动态）
        - FIELD_MAP 自动映射 data key 和 ColumnMeta.name（同步，不会不一致）
        """
        # ── FIELD_MAP 标准化（data 和 columns 同步映射）──
        if self.FIELD_MAP:
            rows = [
                {self.FIELD_MAP.get(k, k): v for k, v in row.items()}
                for row in rows
            ]
            columns = [
                ColumnMeta(
                    name=self.FIELD_MAP.get(col.name, col.name),
                    dtype=col.dtype,
                    label=col.label,
                )
                for col in columns
            ]
        
        base = dict(
            summary=summary,
            source=self.domain,
            columns=columns,
            metadata=business_fields,
        )
        
        if len(rows) <= self.INLINE_THRESHOLD:
            return ToolOutput(format=OutputFormat.TABLE, data=rows, **base)
        else:
            file_ref = self._write_to_staging(rows, columns)
            return ToolOutput(format=OutputFormat.FILE_REF, file_ref=file_ref, **base)

    async def _query_local_data(self, doc_type: str, **kwargs) -> ToolOutput:
        """封装 local_data 调用，强制 doc_type 白名单校验"""
        if doc_type not in self.allowed_doc_types:
            return ToolOutput(
                summary=f"⚠ {self.domain} Agent 无权查询 {doc_type} 类型数据",
                format=OutputFormat.TEXT,
                source=self.domain,
                status=OutputStatus.ERROR,    # ← 必须标记 ERROR，否则上层无法感知
                error_message=f"doc_type={doc_type} 不在 {self.domain} 的白名单 {self.allowed_doc_types} 中",
            )
        return await local_data(self.db, doc_type=doc_type, **kwargs)
```

---

## 10. Phase 4：集成测试 + 文档更新

### 10.1 集成测试矩阵

**单域场景（4个）**

| 场景 | 涉及Agent | 预期行为 |
|------|-----------|---------|
| 查A001库存 | WarehouseAgent | 返回 ToolOutput(TABLE, data=[{sku, sellable, ...}]) |
| 查采购单进度 | PurchaseAgent | 返回 ToolOutput(TABLE, data=[{po_no, eta, ...}]) |
| 查上月淘宝订单统计 | TradeAgent | 返回 ToolOutput(TABLE) + summary |
| 参数不足协商 | 任意部门Agent | validate_params → missing → 返回协商提示 |

**跨2域场景（3个）**

| 场景 | 涉及Agent | 编排模式 | 预期行为 |
|------|-----------|---------|---------|
| 缺货+采购到货对比 | Warehouse + Purchase + Compute | Round0并行 → Round1计算 | 结构化merge对比，输出结论 |
| 订单重量导出Excel | Trade + Compute | Round0串行 → Round1计算导出 | 原子导出xlsx |
| 库存+销量算可售天数 | Warehouse + Trade + Compute | Round0并行 → Round1计算 | 按SKU输出可售天数 |

**跨3域场景（2个）**

| 场景 | 涉及Agent | 编排模式 | 预期行为 |
|------|-----------|---------|---------|
| 退货率TOP10→查库存→查采购→导出 | Aftersale + Warehouse + Purchase + Compute | Round0串行 → Round1并行 → Round2计算 | DAG编排，3份结构化数据merge后导出Excel |
| 缺货订单→采购补货→售后退款影响 | Trade + Purchase + Aftersale + Compute | Round0串行 → Round1并行 → Round2计算 | 跨3域关联分析 |

**写入场景（2个）**

| 场景 | 涉及Agent | 预期行为 |
|------|-----------|---------|
| 修改采购单到货时间 | PurchaseAgent + erp_execute | 参数校验 → ask_user确认 → 执行写入 |
| 批量调整库存 | WarehouseAgent + erp_execute | 参数校验 → 生成变更摘要 → ask_user确认 → 执行 |

**边界场景（3个）**

| 场景 | 预期行为 |
|------|---------|
| 大数据量（>200行）查询结果 | 自动走 FILE_REF，写 parquet，ComputeAgent 从文件读 |
| 某个部门Agent超时 | 其他Agent结果保留，超时的返回 partial，不影响全局 |
| ComputeAgent 计算失败 | 返回已查到的原始数据 + 计算失败提示，用户可重试计算 |

**生产兼容场景（2个）**

| 场景 | 预期行为 |
|------|---------|
| Phase 0 恢复老格式冻结会话（无 file_registry） | snapshot.get("file_registry", []) 返回空列表 → 空 Registry → 正常执行（老会话无 ComputeAgent） |
| Phase 3B 恢复老格式冻结会话（无 dag_progress） | snapshot.get("dag_progress") 为 None → 走旧 tool_loop 链路 → 正常执行 |

### 10.2 文档更新

| 文档 | 更新内容 |
|------|---------|
| PROJECT_OVERVIEW.md | 新增 departments/ 目录结构 |
| FUNCTION_INDEX.md | 新增 ComputeAgent、DepartmentAgent 及所有部门Agent |
| CURRENT_ISSUES.md | 更新已解决/新增的问题 |

---

## 11. 工作量估算

| Phase | 新建文件 | 改造文件 | 预估行数 | 可并行 |
|-------|---------|---------|---------|--------|
| Phase 0 | 3 | 5 | ~600行 | 否（地基） |
| Phase 1A | 2 | 3 | ~400行 | ✅ 与1B并行 |
| Phase 1B | 4 | 0 | ~500行 | ✅ 与1A并行 |
| Phase 2 | 0 | 4 | ~300行改造 | 否（合并点） |
| Phase 3A | 3 | 0 | ~400行 | ✅ 与3B并行 |
| Phase 3B | 2 | 2 | ~450行 | ✅ 与3A并行 |
| Phase 4 | 1 | 3 | ~400行 | 否（收尾） |
| **合计** | **15** | **17** | **~3050行** | — |

Phase 3B 新增文件：
- `services/agent/execution_plan.py`：ExecutionPlan / Round 数据类 + DAG校验
- `services/agent/plan_builder.py`：LLM意图分析→生成ExecutionPlan的适配器

---

## 12. 风险与缓解（经架构评审修订）

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 一次性改造中间状态测试全红 | 开发期阻塞 | 按文件顺序改造：先 tool_output.py → 逐个改工具函数 → 每改一个跑该文件测试 → 最后改 ToolLoopExecutor 全部贯通 |
| 部门Agent 划分不准，需要调整 | 返工 | 先做仓储（边界最清晰），验证框架后再扩展 |
| ComputeAgent 与 code_execute 耦合 | 沙盒异常传播 | ComputeAgent 内部 try-except + 超时独立控制 |
| 并行调度引入竞态 | 数据不一致 | asyncio.gather + 不共享可变状态 |
| _plan_execution LLM规划失败 | 无法调度 | 三级降级链：LLM规划 → _quick_classify单域直通 → abort返回"无法理解请求" |
| 跨Round错误传播 | 错误结论 | OutputStatus + 传播检查 + 根因定位 |
| ComputeAgent 计算结果不合理 | 错误建议 | 两层校验：prompt自检 + validate_compute_result 纯函数硬校验 |
| DAG 复杂度无上限 | 资源耗尽 | MAX_ROUNDS=5, MAX_AGENTS_PER_ROUND=4, MAX_TOTAL_AGENTS=10 |
| 跨Agent字段名不一致 | context提取失败 | 产出端标准化：每个Agent定义FIELD_MAP，输出时映射成全局标准字段名 |

---

## 13. 架构评审争辩结论（完整版）

> 以下是经过多轮架构评审后确定的所有设计决策，覆盖了初版方案中的漏洞和不足。

### 13.1 架构决策

| # | 决策项 | 最终结论 |
|---|--------|---------|
| A1 | ERPAgent 是否保留 | **保留**，作为路由层。主Agent仍只看到1个erp_agent工具。理由：电商域边界模糊，实测扁平化40%准确率vs嵌套92% |
| A2 | ERPAgent 瘦身后职责 | **只做两件事**：意图识别（_quick_classify关键词匹配 + _plan_execution LLM规划）+ 调度部门Agent |
| A3 | 部门Agent数量 | **4个**：warehouse / purchase / trade / aftersale。ERPAgent从18选1变5选1（含ComputeAgent） |
| A4 | ComputeAgent 定位 | **独立计算Agent**，只接收结构化输入，只有code_execute一个工具。与部门Agent区别：部门Agent决定"查什么"，ComputeAgent决定"怎么算" |
| A5 | DAG 类型 | **静态DAG**。计划在执行前一次性生成，执行过程中不根据中间结果调整。计划只确定**谁干**，不确定参数 |

### 13.2 数据流设计

| # | 决策项 | 最终结论 |
|---|--------|---------|
| B1 | 工具返回类型 | **一次性全改为ToolOutput**，无过渡期，无`_raw`双版本 |
| B2 | Agent之间传数据 | **Python对象传递**（ToolOutput），不经过文本序列化。部门Agent读`.data`拿list[dict] |
| B3 | DATA_REF 标签定位 | **给LLM的可读摘要**，不是数据传输协议。Agent之间不靠它传数据。ComputeAgent走的是Python对象流，不读DATA_REF |
| B4 | messages 两层设计 | **通道层**（timestamp固定，ToolLoopExecutor注入）+ **内容层**（DATA_REF动态，Agent自主决定带什么字段） |
| B5 | ToolOutput 字段分层 | **协议层固定**（summary/format/source/columns/data/file_ref/status）+ **业务层动态**（全走metadata字典） |
| B6 | 数据分流规则 | ≤200行 inline（data字段放JSON）；>200行 FILE_REF（写staging parquet） |

### 13.3 跨Agent字段名标准化

**问题**：不同执行层函数用不同列名表示同一个业务概念（outer_id / product_code / item_no）。

**解法**：产出端标准化，不在提取端硬编码。

```python
# 全局标准字段名
CANONICAL_FIELDS = {"product_code", "sku_code", "platform", "shop_name", "warehouse_name"}

class WarehouseAgent(DepartmentAgent):
    # 本域字段映射：底层字段名 → 标准字段名
    FIELD_MAP = {
        "outer_id": "product_code",
        "sku_outer_id": "sku_code",
    }
    # 不需要单独的 _normalize_rows 方法
    # FIELD_MAP 的映射由基类 _build_output 统一处理（同步映射 data key + ColumnMeta.name）
```

- 每个部门Agent只需定义`FIELD_MAP`字典，映射逻辑由基类`_build_output`统一执行
- **不要单独写 `_normalize_rows`**——`_build_output`已经同步映射data和columns，单独的方法只映射data不映射columns会导致不一致
- ColumnMeta用标准名：`ColumnMeta("product_code", "text", "商品编码")`
- 提取端只用标准名：`self._extract_field_from_context(context, "product_code")`
- 加新Agent只需定义自己的FIELD_MAP，不影响其他Agent

### 13.4 Context 注入机制

**决策**：方式B——Python确定性提取，不靠LLM。

```python
class DepartmentAgent(ABC):
    def _extract_field_from_context(
        self, context: list[ToolOutput], field_name: str,
    ) -> list[Any]:
        """从上游context里提取指定字段的值列表（确定性，不靠LLM）"""
        values = []
        for output in (context or []):
            if not output.data:
                continue
            has_field = any(c.name == field_name for c in (output.columns or []))
            if has_field:
                for row in output.data:
                    val = row.get(field_name)
                    if val is not None:       # ← 注意：不用 if val，防止过滤零值
                        values.append(val)
        return values
```

**零值保护**：`if val is not None` 而非 `if val`。库存为0的商品是缺货分析的核心数据，不能被静默丢弃。

### 13.5 参数校验

```python
class DepartmentAgent(ABC):
    # ── 基类提供通用校验（不重复实现）──
    def _validate_time_range(self, time_range_str: str) -> ValidationResult | None:
        """校验已标准化的时间范围字符串（格式：YYYY-MM-DD ~ YYYY-MM-DD）"""
        try:
            start_str, end_str = time_range_str.split(" ~ ")
            start = date.fromisoformat(start_str.strip())
            end = date.fromisoformat(end_str.strip())
        except (ValueError, AttributeError):
            return ValidationResult.conflict(f"时间范围格式错误: {time_range_str}")
        if (end - start).days > 90:
            return ValidationResult.conflict("时间范围不能超过90天")
        if end < start:
            return ValidationResult.conflict("结束日期不能早于开始日期")
        return None

    def _validate_required(self, params: dict, required: list[str]) -> ValidationResult | None:
        missing = [k for k in required if not params.get(k)]
        return ValidationResult.missing(missing) if missing else None
```

**时间解析不在validate里做**：ERPAgent的LLM层已经把"上个月"转成了"2026-03-01 ~ 2026-03-31"，部门Agent收到的是已标准化的参数。

### 13.6 错误处理与传播

#### OutputStatus 定义（边界明确）

```python
class OutputStatus(Enum):
    OK = "ok"          # 查询成功，有数据
    EMPTY = "empty"    # 查询成功，确实没数据（业务合理）
    PARTIAL = "partial"# 查询成功但不完整（超时截断）
    ERROR = "error"    # 查询失败（异常/权限/接口错误）
```

| 场景 | 状态 | 判定标准 |
|------|------|---------|
| 库存查询返回3条 | OK | 执行无异常 + 有数据 |
| 缺货查询返回0条（确实不缺货） | EMPTY | 执行无异常 + 0条 + 业务合理 |
| 订单查询超时只返回前50条 | PARTIAL | 超时截断 |
| 财务Agent权限不足返回0条 | **ERROR**（不是EMPTY） | 异常原因，不是业务结论 |
| 仓储查询成功但数据是3小时前的缓存 | **OK** + metadata["data_age_seconds"]=10800 | 查询成功，新鲜度通过metadata标记 |

**判定逻辑写在基类，部门Agent不自行发挥**：

```python
def _determine_status(self, rows, error=None, is_truncated=False,
                      total_expected: int | None = None) -> tuple[OutputStatus, dict]:
    if error:
        return OutputStatus.ERROR, {}
    if is_truncated:
        meta = {}
        if total_expected is not None:
            meta["total_expected"] = total_expected
        # 不估算，没有就不填。阈值检查端：没有 total_expected 则跳过阈值判断
        return OutputStatus.PARTIAL, meta
    if not rows:
        return OutputStatus.EMPTY, {}
    return OutputStatus.OK, {}
```

#### 错误传播规则

| 上游状态 | 处理 | 理由 |
|---------|------|------|
| **ERROR** | **跳过本Round及所有依赖它的后续Round** | 错误建议的损失 > 没有建议的损失 |
| **PARTIAL（数据量≥10%预期）** | **继续执行，最终结论标记⚠️** | 数据正确但不完整，结论可以参考 |
| **PARTIAL（数据量<10%预期）** | **按ERROR处理，跳过** | 数据量太少，结论无参考价值 |
| **EMPTY** | **继续执行** | 查询成功，"没数据"本身是有意义的结论 |

```python
for i, round in enumerate(plan.rounds):
    context = self._collect_context(round.depends_on, round_results)
    
    # ── ERROR 传播检查 ──
    error_inputs = [c for c in context if c.status == OutputStatus.ERROR]
    if error_inputs:
        sources = ", ".join(c.source for c in error_inputs)
        round_results[i] = [ToolOutput(
            summary=f"跳过：依赖的 {sources} 查询失败",
            status=OutputStatus.ERROR,
            source="erp_agent",
            error_message=error_inputs[0].error_message,
        )]
        continue
    
    # ── PARTIAL 阈值检查（用 flag 控制外层循环，防止 continue 跳错层级）──
    skip_round = False
    for p in [c for c in context if c.status == OutputStatus.PARTIAL]:
        expected = p.metadata.get("total_expected")  # 没有则为 None，跳过阈值判断
        if expected is None:
            continue  # 没有 total_expected → 不做阈值判断，当普通 PARTIAL
        actual = len(p.data or []) if p.data else (p.file_ref.row_count if p.file_ref else 0)
        if expected > 0 and actual < expected * 0.1:
            round_results[i] = [ToolOutput(
                summary=f"⚠ {p.source} 数据严重不完整（{actual}/{expected}行），跳过后续分析",
                status=OutputStatus.ERROR,
                source="erp_agent",
            )]
            skip_round = True
            break  # 第一个触发阈值就跳，不继续检查其他 PARTIAL
    if skip_round:
        continue  # ← 这个 continue 是外层 for i, round 的
    
    # ── 正常执行 ──
    results = await asyncio.gather(*[a.execute(query, context=context) for a in round.agents])
    round_results[i] = list(results)
```

#### 根因定位与用户呈现

```python
def _build_final_result(self, round_results: dict[int, list[ToolOutput]]):
    """按Round索引升序找根因，聚合同Round所有ERROR"""
    for round_idx in sorted(round_results.keys()):
        errors_in_round = [
            o for o in round_results[round_idx]
            if o.status == OutputStatus.ERROR
        ]
        if errors_in_round:
            # 聚合同 Round 所有失败来源（不只报第一个）
            error_details = [
                f"{e.source}: {e.error_message}" for e in errors_in_round
            ]
            cascade_count = sum(
                1 for idx in round_results if idx > round_idx
                for o in round_results[idx] if o.status == OutputStatus.ERROR
            )
            summary = "查询未完成：\n" + "\n".join(f"  - {d}" for d in error_details)
            if cascade_count > 0:
                summary += f"\n（导致后续 {cascade_count} 个步骤跳过）"
            summary += "\n请修正以上问题后重试。"
            return ERPAgentResult(summary=summary, status="error")
    
    # 检查 PARTIAL 警告
    has_partial = any(
        o.status == OutputStatus.PARTIAL
        for outs in round_results.values() for o in outs
    )
    all_outputs = [o for outs in round_results.values() for o in outs]
    summary = self._merge_summaries(all_outputs)
    if has_partial:
        summary = "⚠ 部分数据不完整，以下结论仅供参考\n\n" + summary
    return ERPAgentResult(summary=summary, status="success")
```

### 13.7 _plan_execution 降级链

```python
async def _plan_execution(self, query: str) -> ExecutionPlan:
    # ── 第一级：LLM 规划 ──
    try:
        plan = await self._llm_plan(query)
        plan.validate()  # 校验 MAX_ROUNDS/MAX_AGENTS 限制 + 无环
        return plan
    except (LLMError, ValidationError) as e:
        logger.warning(f"LLM plan failed: {e}")
    
    # ── 第二级：关键词匹配单域直通 ──
    domain = self._quick_classify(query)
    if domain:
        return ExecutionPlan(rounds=[
            Round(agents=[self._agents[domain]], depends_on=[])
        ])
    
    # ── 第三级：无法理解，直接返回 ──
    return ExecutionPlan.abort("无法理解您的请求，请更具体地描述您要查询的内容")
```

**abort 处理**：执行器循环前拦截：

```python
async def execute(self, query, parent_messages):
    plan = await self._plan_execution(query)
    if plan.is_abort:
        return ERPAgentResult(summary=plan.abort_message, status="error")
    # ... Round 循环 ...
```

### 13.8 DAG 限制

```python
MAX_ROUNDS = 5              # 最多5轮
MAX_AGENTS_PER_ROUND = 4    # 每轮最多4个并行Agent
MAX_TOTAL_AGENTS = 10       # 一次执行最多调10个Agent

def validate(self):
    if len(self.rounds) > MAX_ROUNDS:
        raise ValidationError(f"DAG 不能超过 {MAX_ROUNDS} 轮")
    for round in self.rounds:
        if len(round.agents) > MAX_AGENTS_PER_ROUND:
            raise ValidationError(f"每轮不能超过 {MAX_AGENTS_PER_ROUND} 个 Agent")
    total = sum(len(r.agents) for r in self.rounds)
    if total > MAX_TOTAL_AGENTS:
        raise ValidationError(f"总 Agent 调用不能超过 {MAX_TOTAL_AGENTS} 次")
    # 依赖关系无环检查（Round 按顺序编号，depends_on 必须指向更小的索引）
    for i, round in enumerate(self.rounds):
        for dep in round.depends_on:
            if dep >= i:
                raise ValidationError(
                    f"Round {i} 依赖了 Round {dep}（≥自身索引），DAG 有环或前向引用"
                )
            if dep < 0 or dep >= len(self.rounds):
                raise ValidationError(f"Round {i} 依赖了不存在的 Round {dep}")
```

### 13.9 ComputeAgent 结果校验

**两层校验**：prompt引导LLM自检（软）+ Python代码硬校验（硬）。

#### 硬校验：validate_compute_result（独立纯函数，可单元测试）

```python
def validate_compute_result(task: ComputeTask, result: ComputeResult) -> ValidationChecks:
    """纯函数：不依赖沙盒/数据库/文件系统"""
    checks = ValidationChecks()
    
    # 获取结果行数（inline 或 FILE_REF 统一处理）
    result_rows = 0
    if result.data is not None:
        result_rows = len(result.data)
    elif result.file_ref:
        result_rows = result.file_ref.row_count  # 写文件时立即填入
    
    # 检查1：merge 后行数
    input_rows = []
    for inp in task.inputs:
        if inp.data:
            input_rows.append(len(inp.data))
        elif inp.file_ref:
            input_rows.append(inp.file_ref.row_count)
    
    if input_rows and result_rows == 0 and all(r > 0 for r in input_rows):
        checks.add_critical("合并后0行数据，输入数据可能无匹配字段（检查字段名是否一致）")
    
    # 检查2：导出文件未生成
    if task.output_format in ("xlsx", "csv") and not result.file_ref:
        checks.add_critical("要求导出文件但未生成")
    
    # 检查3：code_execute 报错
    if result.error_message:
        checks.add_critical(result.error_message)
    
    return checks
```

**file_ref.row_count 来源**：写parquet的函数在写完后立即填入。DuckDB的`COPY TO`返回写入行数，pandas的`to_parquet`后用`len(df)`。准确性由写入操作保证，不是事后读文件猜的。

#### 软校验：ComputeAgent system prompt

```
## 计算完成后必须自检

1. merge后行数：输入N行+M行，merge后应接近min(N,M)。
   0行 → 停止，输出"⚠ 数据关联失败，可能编码格式不一致"
2. 数值范围：可售天数<0、退货率>1.0、金额为负（非退款） → 异常
3. 空值占比：任何列空值>50% → 标注"⚠ {列名}空值率高"
4. 任何检查失败，结论开头加⚠️，不能输出"一切正常"
```

### 13.10 FileRef 文件失效保护

```python
@dataclass(frozen=True)
class FileRef:
    path: str
    filename: str
    format: str
    row_count: int
    size_bytes: int
    columns: list[ColumnMeta]
    preview: str = ""
    created_at: float = 0.0    # 创建时间戳

    def is_valid(self, max_age_seconds: int = 86400) -> bool:
        """检查文件是否仍然有效"""
        import time
        from pathlib import Path
        if not Path(self.path).exists():
            return False
        if self.created_at and (time.time() - self.created_at) > max_age_seconds:
            return False
        return True
```

### 13.11 工具归属表

| 工具 | 归属 | 说明 |
|------|------|------|
| local_data | 所有部门Agent共用 | 通过 `allowed_doc_types` 过滤 |
| local_product_identify | 公共工具 | 所有部门Agent可调 |
| local_compare_stats | 公共工具 | 所有部门Agent可调 |
| erp_info_query | 公共工具 | 跨多部门 |
| erp_product_query | 公共工具 | 跨多部门 |
| erp_warehouse_query | 仓储Agent | 库存调拨/盘点 |
| erp_purchase_query | 采购Agent | 采购单/供应商 |
| erp_trade_query + erp_taobao_query | 订单Agent | 订单/物流 |
| erp_aftersales_query | 售后Agent | 退货/退款 |
| erp_api_search | 部门Agent内部调用 | 下沉，ERPAgent不再持有 |
| code_execute | ComputeAgent独有 | 从SHARED域移到COMPUTE域 |
| erp_execute（写操作） | 各部门Agent可调 | 幂等锁机制不变 |

---

## 14. 缺陷→Phase 映射（审计追踪）

| 缺陷 | Phase | 修复方式 |
|------|-------|---------|
| D1 ERPAgent既查询又计算 | 1A+2 | ComputeAgent提取 + ERPAgent瘦身 |
| D2 Prompt混杂 | 1A+1B+2 | 按Agent拆分prompt |
| D3 文本传参 | 0 | ToolOutput结构化协议（Agent间传Python对象，DATA_REF只是LLM摘要） |
| D4 stock返回markdown | 0 | 改返回ToolOutput(TABLE) |
| D5 所有local_*返回markdown | 0 | 统一改返回ToolOutput + 产出端字段名标准化 |
| D6 无文件注册表 | 0 | SessionFileRegistry（key=domain:tool:ts） |
| D7 erp_api_search元层 | 2 | 下沉为部门Agent内部调用 |
| D8 导出3步隐式编排 | 0+2 | 执行层只输出parquet，格式转换由ComputeAgent做 |
| D9 计算超时丢结果 | 1A | ComputeAgent独立预算 + validate_compute_result硬校验 |
| D10 上下文压缩丢数据 | 0 | ToolOutput.summary 保留关键信息 |
| D11 缓存TTL过期 | 0 | ToolOutput.metadata 标记时间戳 |
| D12 截断切文件路径 | 0 | FileRef 结构化引用 + is_valid()失效检测 |
| D13 工具扁平无分组 | 1B | 部门Agent各持本域工具 |
| D14 无并行能力 | 3B | 静态DAG编排 + asyncio.gather并行 + MAX_ROUNDS/MAX_AGENTS限制 |
| D15 参数校验通用化 | 1B | 基类通用校验 + 部门Agent特有规则 |
| D16 经验记录耦合 | 2 | 提取为独立ExperienceRecorder + subcategory域标识 |
| D17 loop_snapshot缺file_registry | 3B | 冻结时序列化 SessionFileRegistry，恢复时重建 |
| D18 steer打断缺DAG进度 | 3B | Round完成后保存进度，打断时保留已完成Round结果 |
| D19 经验记录无域标识 | 2 | subcategory用标准域标识（warehouse/purchase/trade/aftersale） |
| D20 | 跨Round错误传播 | 3B | OutputStatus四状态 + ERROR跳过 + PARTIAL阈值 + 根因定位（按Round索引） |
| D21 | ComputeAgent结果不合理 | 1A | 两层校验：prompt自检 + validate_compute_result纯函数硬校验 |
| D22 | 跨Agent字段名不一致 | 1B | 产出端标准化：FIELD_MAP映射 + CANONICAL_FIELDS全局标准名 |

---

## 15. 生产迁移与回滚策略

### 15.1 各 Phase 部署策略

| Phase | 风险等级 | 部署方式 | 回滚方式 |
|-------|---------|---------|---------|
| Phase 0 | 🔴 高（一次性改全部返回类型） | 低峰停服发布（凌晨2点） | git revert 整个 commit + 重新部署（<10分钟） |
| Phase 1A | 🟢 低（新增文件为主） | 正常发布 | 回退 commit |
| Phase 1B | 🟢 低（新增文件为主） | 正常发布 | 回退 commit |
| Phase 2 | 🟡 中（ERPAgent 核心改造） | 低峰停服发布 | git revert |
| Phase 3A | 🟢 低（新增 Agent） | 正常发布 | 回退 commit |
| Phase 3B | 🔴 高（DAG 引擎 + 打断恢复改造） | 低峰停服发布 | git revert |
| Phase 4 | 🟢 低（测试 + 文档） | 正常发布 | 无需回滚 |

### 15.2 已冻结会话兼容（pending_interaction 老格式）

部署新代码时，pending_interaction 表里可能有老格式的冻结会话。

**Phase 0 部署时**（只改返回类型，不改执行流程）：
```python
def _restore_from_pending(self, pending):
    snapshot = json.loads(pending.loop_snapshot)
    
    # 老格式兼容：没有 file_registry 字段 → 空 Registry
    file_registry = SessionFileRegistry()
    for entry in snapshot.get("file_registry", []):
        ref = FileRef(**entry["file_ref"])
        file_registry._files[entry["key"]] = ref
    # 老格式会话没有 ComputeAgent，空 Registry 不影响执行
    
    # 老格式兼容：frozen_messages 里没有 timestamp 字段
    # ToolLoopExecutor 读 timestamp 用 .get()，None 时不影响执行
```

**Phase 3B 部署时**（引入 DAG 模式）：
```python
    # 老格式兼容：没有 dag_progress 字段
    dag_progress = snapshot.get("dag_progress")
    if dag_progress:
        # 新格式：从上次完成的 Round 继续
        ...
    else:
        # 老格式：走旧的 tool_loop 模式（不是 DAG）
        # 老会话在 Phase 3B 之前冻结，恢复后仍按旧链路执行
        ...
```

### 15.3 部署前检查清单

```
Phase 0 部署前：
  □ 开发环境全量测试通过（4153+新增 全绿）
  □ 清理过期 pending_interaction（status=expired 的删掉）
  □ 确认 staging 目录可写
  □ 准备好 git revert 的 commit hash
  □ _restore_from_pending 老格式兼容分支测试通过（无 file_registry 字段的 snapshot 能正常恢复）

Phase 3B 部署前：
  □ 同上
  □ _restore_from_pending 老格式兼容分支测试通过（无 dag_progress 字段的 snapshot 走旧 tool_loop 链路）
  □ 注意：不要求 pending 记录清零（生产环境总有进行中的会话），兼容由代码保证
```

---

## 16. 缺陷→Phase 映射（审计追踪）

| 缺陷 | Phase | 修复方式 |
|------|-------|---------|
| D1 ERPAgent既查询又计算 | 1A+2 | ComputeAgent提取 + ERPAgent瘦身 |
| D2 Prompt混杂 | 1A+1B+2 | 按Agent拆分prompt |
| D3 文本传参 | 0 | ToolOutput结构化协议（Agent间传Python对象，DATA_REF只是LLM摘要） |
| D4 stock返回markdown | 0 | 改返回ToolOutput(TABLE) |
| D5 所有local_*返回markdown | 0 | 统一改返回ToolOutput + 产出端字段名标准化 |
| D6 无文件注册表 | 0 | SessionFileRegistry（key=domain:tool:ts） |
| D7 erp_api_search元层 | 2 | 下沉为部门Agent内部调用 |
| D8 导出3步隐式编排 | 0+2 | 执行层只输出parquet，格式转换由ComputeAgent做 |
| D9 计算超时丢结果 | 1A | ComputeAgent独立预算 + validate_compute_result硬校验 |
| D10 上下文压缩丢数据 | 0 | ToolOutput.summary 保留关键信息 |
| D11 缓存TTL过期 | 0 | ToolOutput.metadata 标记时间戳 |
| D12 截断切文件路径 | 0 | FileRef 结构化引用 + is_valid()失效检测 |
| D13 工具扁平无分组 | 1B | 部门Agent各持本域工具 |
| D14 无并行能力 | 3B | 静态DAG编排 + asyncio.gather并行 + MAX_ROUNDS/MAX_AGENTS限制 |
| D15 参数校验通用化 | 1B | 基类通用校验 + 部门Agent特有规则 |
| D16 经验记录耦合 | 2 | 提取为独立ExperienceRecorder + subcategory域标识 |
| D17 loop_snapshot缺file_registry | 3B | 冻结时序列化 SessionFileRegistry，恢复时重建 |
| D18 steer打断缺DAG进度 | 3B | Round完成后保存进度，打断时保留已完成Round结果 |
| D19 经验记录无域标识 | 2 | subcategory用标准域标识（warehouse/purchase/trade/aftersale） |
| D20 跨Round错误传播 | 3B | OutputStatus四状态 + ERROR跳过 + PARTIAL阈值（flag+break防跳错层级）+ 根因聚合（同Round多Agent） |
| D21 ComputeAgent结果不合理 | 1A | 两层校验：prompt自检 + validate_compute_result纯函数硬校验 |
| D22 跨Agent字段名不一致 | 1B | 产出端标准化：FIELD_MAP同步映射data key和ColumnMeta.name |
| D23 allowed_doc_types无强制 | 1B | 基类`_query_local_data`封装 + 白名单校验，不依赖调用方传参 |
| D24 生产迁移无策略 | 0+3B | 低峰停服发布 + pending_interaction老格式兼容 + git revert回滚 |

---

**方案版本**：V2.2（第五轮评审修订 · 终版）· 2026-04-16

**下一步**：按 Phase 0 → Phase 1A/1B（并行）→ Phase 2 → Phase 3A/3B（并行）→ Phase 4 顺序开工。

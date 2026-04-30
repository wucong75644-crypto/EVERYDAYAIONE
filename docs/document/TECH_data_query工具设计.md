# data_query 工具设计方案

> 版本：v2.0 | 日期：2026-04-30 | 状态：✅ 方案锁定，进入实施

## 一、背景与问题

### 当前痛点

1. **staging 大文件读取崩内存**：erp_agent 查询结果存入 staging Parquet 后，code_execute 用 `pd.read_parquet()` 全量加载到内存，大文件直接 OOM
2. **工作区大文件同样问题**：用户上传的 Excel 文件，code_execute 用 `pd.read_excel()` 全量加载
3. **Agent 反复重试**：看到 data_profile 预览只有几条数据，误以为查询结果不完整，反复调 erp_agent 重试（实测一个简单任务调了 ~10 次）
4. **中间结果无处安放**：Agent 不知道 STAGING_DIR 可以写中间结果，只会写 OUTPUT_DIR 生成最终文件

### 根因分析

- 缺少一个**精确查询**工具，Agent 只能全量读取或反复重查
- data_profile 已经提供了完整的 schema 信息（列名、类型、统计、预览），但 Agent 没有工具利用这个 schema 写 SQL 查询

## 二、方案设计

### 工具定义

```python
data_query(file: str, sql: str = None, export: str = None, sheet: str = None)
```

- **file**：文件名或相对路径（如 `"trade_123.parquet"` 或 `"销售报表.xlsx"`）
- **sql**：SQL 查询语句（可选，表名统一用 `FROM data`）
- **export**：导出文件名（如 `"各店铺销售对比.xlsx"`），传则导出，不传则查询（可选）
- **sheet**：Excel Sheet 名称或索引（可选，默认第一个 Sheet）

### 文件路径统一规范

与 file_read / file_list 保持一致，所有文件工具都用**文件名或相对路径**，Agent 不需要知道绝对路径。

```
data_query 内部解析：
    收到 file 参数
        ├─ 在 workspace 根目录找 → 找到 → 用这个
        └─ 找不到 → 在 staging/{conv_id}/ 找 → 找到 → 用这个
             └─ 都找不到 → 报错："文件 'xxx' 不存在"
```

| 工具 | file 参数格式 | 示例 |
|------|-------------|------|
| file_read | 文件名或相对路径 | `file_read(path="销售报表.xlsx")` |
| file_list | 目录相对路径 | `file_list(path="子目录/")` |
| data_query | 文件名或相对路径 | `data_query(file="trade_123.parquet")` |

data_profile `[读取]` 行同步更新为：
```
[查询] data_query(file="trade_123.parquet", sql="SELECT ... FROM data")
```

### 三种模式

| 模式 | 触发条件 | 行为 | 返回 |
|------|---------|------|------|
| 探索模式 | 不传 sql | DuckDB 读文件 metadata | data_profile（列名、类型、行数、统计、预览） |
| 查询模式 | 传 sql | DuckDB 执行 SQL | 四档分层返回（详见边缘情况#2）|
| 导出模式 | 传 sql + export="文件名.xlsx" | DuckDB COPY TO xlsx | 文件写 OUTPUT_DIR → 自动上传 |

### 覆盖两个数据源

| 数据源 | 文件格式 | DuckDB 处理方式 |
|--------|---------|----------------|
| staging（erp_agent 产出） | Parquet | 直接查询 |
| 工作区（用户上传） | Excel / CSV / Parquet | CSV/Parquet 直接查；Excel 先转 Parquet 缓存到 staging |

### Excel 转换缓存

- 第一次 data_query 引用 Excel 文件时，用 calamine 引擎读取 → 写 Parquet 到 staging
- 缓存文件命名：`_cache_{md5(full_path)[:8]}_{原始文件名}.parquet`（路径哈希防同名冲突）
- 同一对话内后续查询直接走缓存，不重复转换
- 转换后 df 立即释放内存，后续查询全走 DuckDB 磁盘读取

## 三、存储位置

```
workspace/
├── 销售报表.xlsx                ← 用户原始文件（不动）
├── staging/{conv_id}/
│   ├── trade_123.parquet        ← erp_agent 产出的数据
│   └── _cache_a1b2c3d4_销售报表.parquet  ← Excel 转换缓存（路径哈希防冲突）
└── 下载/
    └── 分析报告.xlsx             ← code_execute 输出的最终结果
```

## 四、系统架构与工具分层

### 两层分离原则

**数据获取层只读数据，计算生成层只算数据。**

```
┌─────────────────────────────────────────────────────────────┐
│                      数据获取层（只读）                       │
│                                                             │
│  erp_agent       查 ERP 业务数据                             │
│                  ├─ 小数据 → 直接返回摘要到上下文              │
│                  └─ 大数据 → 写 staging Parquet + data_profile│
│                                                             │
│  data_query      精确查询数据文件                             │
│                  ├─ 探索模式：返回 schema                     │
│                  ├─ 查询模式：SQL → 小结果返回上下文            │
│                  └─ 导出模式：DuckDB COPY TO xlsx → OUTPUT_DIR│
│                                                             │
│  file_read       读非数据文件（文本/PDF/图片）                 │
│  web_search      互联网实时信息                               │
│  search_knowledge 企业知识库                                  │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼ 小数据在上下文中
┌─────────────────────────────────────────────────────────────┐
│                    计算生成层（只写）                          │
│                                                             │
│  code_execute    纯计算引擎                                   │
│                  ├─ 输入：上下文中的小数据                      │
│                  ├─ 计算：pandas 聚合/对比/涨跌幅              │
│                  ├─ 可视化：matplotlib 画图                    │
│                  └─ 输出：Excel/图表 → OUTPUT_DIR → 自动上传   │
│                                                             │
│  generate_image / generate_video   AI 生成媒体                │
└─────────────────────────────────────────────────────────────┘
```

### 工具职责矩阵

| 工具 | 职责 | 读数据 | 写数据 | 何时用 |
|------|------|--------|--------|--------|
| erp_agent | 查 ERP 业务数据 | ✅ | staging | 需要业务数据时 |
| data_query | 精确查询/导出数据文件 | ✅ | staging/OUTPUT_DIR | staging 引用或工作区大文件 |
| code_execute | 纯计算 + 生成文件 | ❌ 不读原始大文件 | OUTPUT_DIR | 拿到小数据后计算/可视化 |
| file_read | 读非数据文件 | ✅ | ❌ | 文本/PDF/图片 |

### 典型流程

**ERP 数据分析：**
1. erp_agent → 大数据写 staging → 返回 data_profile
2. data_query(sql) → DuckDB 精确提取需要的子集 → 返回小结果
3. code_execute → 用小结果计算/画图/出报表

**工作区文件分析：**
1. data_query(file="报表.xlsx") → 探索模式，转 Parquet 缓存 + 返回 schema
2. data_query(file="报表.xlsx", sql="SELECT ...") → 查询模式，精确提取
3. code_execute → 用小结果计算/出报表

**全量数据导出：**
1. erp_agent → 大数据写 staging → 返回 data_profile（含文件名如 `trade_123.parquet`）
2. data_query(file="trade_123.parquet", sql="SELECT * FROM data", export="本月订单明细.xlsx")
   → DuckDB COPY TO xlsx → 直接写 OUTPUT_DIR → 公共 _auto_upload 上传
3. 不需要 code_execute — DuckDB 2秒完成，15MB内存

**大结果分析（非导出）：**
1. data_query(file="trade_123.parquet", sql="复杂聚合/窗口函数") → SQL 层完成筛选和计算
2. 只返回小结果（如 Top 50 异常 SKU）
3. code_execute → 用小结果画图/出报表
4. 核心原则：不是数据大就全拿出来，Agent 用 SQL 在源头聚合筛选

## 五、schema 持久化（智能过滤注入）

### 核心问题

data_profile 约 400-600 tokens/个。全部注入占 10000 tokens（20 个文件），挤压上下文。
不注入则 Agent 写 SQL 前需要多一次探索调用，增加延迟。

### 方案：embedding 相似度过滤 + 按需注入完整 schema

#### 智能过滤（复用记忆过滤架构）

```
schema 注入流程：

用户消息进来
    │
    ▼
embedding 相似度计算（20-50ms）
    ├─ 用户消息 → text-embedding-v3 → 向量
    ├─ 和 registry 中预计算好的 schema 向量做余弦相似度
    │
    ├─ 有文件 >0.65 → 注入这些文件的完整 schema ✅
    │   （通常 1-3 个，500-1500 tokens）
    │
    └─ 所有文件 <0.65 → 可能是复杂查询
        │
        └─ 降级：调 Qwen-Flash 判断（100-200ms）
            ├─ 返回相关文件索引 → 注入 ✅
            └─ 超时 → 注入最近 3 个使用过的 schema ✅
```

#### 模型选择（2026-04-30 调研）

| 方案 | 模型 | 延迟 | 每次成本 | 日成本(1万条) |
|------|------|------|---------|-------------|
| **主路径** | text-embedding-v3（DashScope）| 20-50ms | 0.00007 元 | ~0.7 元 |
| **降级** | Qwen-Flash（DashScope）| 100-200ms | 0.0001 元 | ~1 元 |
| 备选 | DeepSeek V4 Flash | ~500ms | 0.001 元 | ~10 元 |

推荐 embedding 主路径 + Qwen-Flash 降级，月成本 ~50 元。

**为什么用 embedding 而不是直接用 LLM：**
- 快 5-10 倍（20ms vs 150ms）
- 便宜 30%
- 确定性（同输入同输出，无 LLM 随机性）
- 90% 场景够用（用户消息和文件 schema 语义匹配）

**为什么需要 Qwen-Flash 降级：**
- 复杂跨表查询（"把订单和退货对比"→ 需要理解"对比"意味着两个表）
- embedding 余弦相似度无法捕捉推理关系

#### schema 完整生命周期

```
创建（两个入口）：
    erp_agent 写 staging 时：
    ├─ file_ref 自动注册 registry（tool_loop_executor 已有逻辑）
    ├─ 异步预计算 embedding（fire-and-forget，不阻塞工具返回）
    └─ data_profile 进入对话上下文

    data_query 探索模式时：
    ├─ schema 进入对话上下文（当轮可用）
    ├─ 存入 session_file_registry（对话级外部索引）
    └─ 异步预计算 embedding 向量并存储（供后续过滤用）

过滤注入（每轮消息触发）：
    embedding 相似度 → 只注入相关文件的完整 schema
    不相关文件不注入，不占上下文

压缩：
    上下文中的 schema 随普通工具结果正常压缩（只保留文件路径引用）
    registry 中的 schema + embedding 不受影响

清理（schema 跟着文件走，文件没了 schema 也没了）：
    ├─ staging 文件被清理时 → 同步删除 registry 中 schema + embedding
    ├─ 对话结束时 → 整个 session_file_registry 随对话销毁
    └─ Excel 缓存（_cache_*.parquet）→ 随 staging 一起清理

兜底：
    registry 中查不到 schema（异常） → Agent 调 data_query 重查
    DuckDB 读 Parquet metadata，毫秒级返回
```

#### Registry 只存原始数据源

| 文件类型 | 存 registry？ | 理由 |
|---------|-------------|------|
| erp_agent 产出的 staging 文件 | ✅ 存 | Agent 不知道里面有什么 |
| data_query 探索的工作区文件 | ✅ 存 | Agent 需要 schema 写 SQL |
| data_query 超限结果写的 staging | ❌ 不存 | 派生物，Agent 自己查的，知道内容 |
| code_execute 写的中间结果 | ❌ 不存 | 派生物，Agent 自己算的 |

### 需要修改

1. **session_file_registry.py**：扩展存储 data_profile schema + 预计算 embedding 向量
2. **schema 过滤模块**（新建）：embedding 相似度 + Qwen-Flash 降级，复用 memory_filter 架构
3. **上下文构建逻辑**：每轮消息调过滤模块 → 注入相关 schema
4. **压缩逻辑**：正常压缩 schema，只保留文件路径引用
5. **staging 清理逻辑**：文件删除时同步清理 registry 中 schema + embedding

## 六、DuckDB 导出模式技术细节

### 调研结论（2026-04-30）

DuckDB `excel` 核心扩展支持 `COPY TO xlsx`，全程 C++ 流式处理，不经过 Python 内存。

| 对比 | DuckDB COPY TO xlsx | Pandas to_excel |
|------|---------------------|-----------------|
| 50万行耗时 | **1.93 秒** | 27.18 秒（14 倍慢）|
| 内存占用 | **+15 MB** | +2,041 MB（136 倍大）|

扩展规模：10万行 0.2s / 50万行 1s / 100万行 2.5s，内存恒定 15-35MB。

### DuckDB 安全连接方案（替代 read_only）

调研发现 DuckDB 有比 `read_only=True` 更精确的安全控制：

```python
con = duckdb.connect(':memory:')
con.execute("LOAD spatial")  # xlsx 导出

# 1. 只允许访问用户 workspace（含 staging + 下载 子目录）
con.execute(f"SET allowed_directories = ['{workspace_dir}']")
# 2. 禁止所有其他文件系统访问
con.execute("SET enable_external_access = false")
# 3. 锁定配置，SQL 注入无法解锁
con.execute("SET lock_configuration = true")
```

| 操作 | 结果 |
|------|------|
| SELECT FROM staging 文件 | ✅ 允许 |
| COPY TO OUTPUT_DIR/xxx.xlsx | ✅ 允许 |
| COPY TO /tmp/evil.csv | ❌ allowed_directories 拦截 |
| read_csv('/etc/passwd') | ❌ 拦截 |
| SET enable_external_access = true | ❌ lock_configuration 拦截 |

比 read_only 好在：支持 COPY TO 导出 + 目录级精确控制 + 配置锁定防逃逸。

### FROM data 表别名方案（替代字符串替换）

用 DuckDB `CREATE TEMP VIEW` 注册表别名，SQL 解析器天然区分表名和列名，零歧义：

```python
con.execute("""
    CREATE TEMP VIEW data AS 
    SELECT * FROM read_parquet('staging/.../file.parquet')
""")

# LLM 写的 SQL 直接用 FROM data，不会和 data_type 等列名冲突
con.execute("SELECT data_type, amount FROM data WHERE data_source = 'erp'")
```

TEMP VIEW 不加载数据到内存，DuckDB 执行 SQL 时按需从磁盘读取列数据。

### 导出模式内部流程

```
data_query(file="trade.parquet", 
           sql="SELECT * FROM data", 
           export="本月订单明细.xlsx")
    │
    ├─ CREATE TEMP VIEW data AS SELECT * FROM read_parquet('...')
    ├─ COPY (SELECT * FROM data) TO 'OUTPUT_DIR/本月订单明细.xlsx'
    │       WITH (FORMAT GDAL, DRIVER 'xlsx')
    ├─ 全程 C++ 流式：Parquet → DuckDB 引擎 → xlsx writer → 磁盘
    ├─ 不创建 pandas DataFrame，不经过 Python 内存
    └─ 调用公共 upload 函数 → 生成 [FILE] 引用 → 自动上传
```

### 文件上传统一

导出文件的上传逻辑与 code_execute 共用同一个 `_auto_upload` 函数：
- 从 `sandbox/functions.py` 提取为公共模块
- data_query 导出完成后主动调用
- 生成 `[FILE]{url}|{filename}|{mime}|{size}[/FILE]` 标签
- 前端统一展示为可下载文件

### 限制

- Excel 行数上限 1,048,576 行/sheet（可覆盖但不保证兼容性）
- 不支持单元格格式（颜色/字体/列宽）——只写数据
- 不支持单次写多 Sheet
- 超 100 万行建议导出为 CSV

### 大结果的两种处理策略

| 场景 | 用户意图 | 处理方式 |
|------|---------|---------|
| "导出所有订单" | 要全量数据文件 | data_query(export="订单明细.xlsx") → DuckDB 直接写 xlsx |
| "分析 SKU 销售波动" | 要分析结论 | data_query(sql=聚合/窗口函数) → SQL 层完成筛选 → 只返回小结果 |

核心原则：**导出用 DuckDB 直写，分析用 SQL 在源头聚合。不要把大数据拉到 code_execute 里处理。**

## 七、已完成的前置改动

### 主 Agent 提示词优化（2026-04-30 已上线）

commit: `bac5ca0`

TOOL_SYSTEM_PROMPT 新增三个段落：
- **任务拆分**：复杂请求拆最小独立子任务，禁止打包
- **并行与顺序**：独立子任务并行，有依赖顺序执行
- **编排与串联**：以终为始规划每步输入输出，staging 中转 + print 摘要

## 八、大厂方案调研（2026-04-30 完成）

### 行业三层级

| 层级 | 代表 | 引擎 | 文件上限 | Schema 持久化 |
|------|------|------|---------|--------------|
| Tier 1 pandas 沙盒 | OpenAI / Claude / Gemini | pandas 全量加载 | ~50-100MB | 仅对话上下文，丢了就丢了 |
| Tier 2 DuckDB SQL | Vanna.ai / DuckDB MCP / WrenAI | DuckDB 磁盘查询 | 几乎无限 | 文件 metadata 自动推断 |
| Tier 3 企业语义层 | Snowflake Cortex / Databricks Genie | 数仓 SQL | PB 级 | 语义模型（YAML）持久化 |

### 关键发现

- **OpenAI Code Interpreter**：纯 pandas 沙盒，512MB 限制，实际 ~50MB 就不稳定。新版 Responses API 优化为"只读前 1000 行 + metadata 摘要"
- **Claude**：9GB RAM 沙盒比 OpenAI 大，但本质同样是 pandas 全量加载。生态靠 MCP 扩展（Large File MCP 做流式分块）
- **DuckDB 生态**：增长最快的方向。零拷贝列式扫描，10GB Parquet 几乎瞬间查询。LLM 生成 SQL 比 pandas 代码出错率更低
- **Snowflake Cortex**：语义模型是核心创新——给列加业务含义描述，SQL 准确率 90%+。我们的 data_profile 已部分覆盖（高频值、统计），后续可借鉴

### 我们的定位

Tier 2（DuckDB SQL），比主流 Tier 1 产品更强。通过 session_file_registry 持久化 schema，比大多数方案做得更好。

### 大厂文件清理参数对比

| 平台 | 文件 TTL | 存储上限 | 清理触发 |
|------|---------|---------|---------|
| OpenAI 沙盒 | 20 分钟不活跃 | 1-64GB 内存 | 超时自动销毁 |
| OpenAI 向量存储 | 7 天（最后使用后） | 10K 文件/store | 最后活跃时间 |
| ChatGPT | 对话存在期 + 删除后 30 天 | 25GB/用户，100GB/组织 | 对话删除触发 |
| Claude Files API | 无 TTL（手动删除） | 500GB/组织 | 手动 |
| Claude 沙盒 | 30 天 | - | 硬过期 |
| Gemini Files API | 48 小时（硬过期） | 20GB/项目 | 自动 |

关键结论：无大厂用 LRU 淘汰文件。两种主流策略——激进 TTL（Gemini 48h / OpenAI 20min）或跟着对话走（ChatGPT / Claude）。

## 九、staging 清理策略（企微长对话场景）

### 参数设计

| 参数 | 值 | 依据 |
|------|-----|------|
| staging 文件 TTL | 24 小时 | 企微场景昨天的数据今天大概率过时；对齐 FileRef 已有默认值 |
| 导出文件 TTL | 48 小时 | 用户可能次日下载，给更多时间 |
| registry schema TTL | 跟着 staging 文件走 | 文件过期 → schema 同步删除 |
| 单用户 staging 上限 | 500MB | 约 50-100 个 Parquet 文件，足够一天使用 |

### 清理保护机制（registry 保护伞）

核心规则：**registry 里有的文件受保护，不删；registry 里没有的按 TTL 清理。**

```
文件清理判断：

    遍历 staging 目录中的文件
        │
        ├─ 文件在 registry 中 → 跳过（受保护，无论存在多久）
        │
        └─ 文件不在 registry 中（孤儿文件）
            ├─ 超过 24h → 删除
            └─ 未超过 24h → 保留

registry 淘汰（控制保护数量）：

    registry 条目超过 20 个
        → LRU 淘汰最久未引用的条目
        → 被淘汰的文件失去保护
        → 下次清理周期按 TTL 处理
```

| 文件状态 | 是否删除 |
|---------|---------|
| 在 registry 中 | 不删，无论多久 |
| 不在 registry 中 + 未超 24h | 不删 |
| 不在 registry 中 + 超 24h | 删 |
| registry 满（>20 个）| LRU 淘汰最旧条目 → 文件失去保护 |

### 极端场景覆盖

| 场景 | 风险 | 保护机制 |
|------|------|---------|
| 并行执行中文件被删 | 3 个 data_query 并行引用同一文件 | 文件在 registry 中 → 受保护不删 |
| 计划模式用户长时间不确认 | 展示方案后用户去开会 6 小时 | 文件在 registry 中 → 受保护不删 |
| 话题岔开 15 轮后回头 | "回到之前那个分析" | 文件在 registry 中 → 受保护不删 |
| 进程重启丢失清理任务 | 该删的文件没删 | 启动时扫描一次，清理不在 registry 中且超 24h 的孤儿文件 |
| registry 中的文件永远不删导致磁盘满 | 活跃对话累积大量文件 | LRU 上限 20 个 + 500MB 容量兜底 |

### 清理触发时机

```
1. 消息驱动（主路径）：
    用户发消息 → asyncio.create_task（fire-and-forget）
    ├─ 主流程：正常处理消息（不受影响）
    └─ 清理任务（线程池执行）：
        ├─ 扫描 staging 目录
        ├─ 跳过 registry 中的文件
        ├─ 删除超 24h 的孤儿文件
        ├─ 同步清理对应的 registry 条目（如有残留）
        └─ 目录超 500MB → 从最旧的非保护文件开始删

2. 进程启动（兜底）：
    服务启动时扫描所有用户 staging 目录
    清理不在任何 registry 中且超 24h 的文件
```

### 不影响主流程的保证

- `asyncio.create_task()` fire-and-forget，主流程不 await
- 文件 IO 操作放到 `run_in_executor`（线程池），不阻塞事件循环
- 清理失败（文件被占用等）静默跳过，不影响消息处理

## 十、边缘情况与防御设计

### 安全类（🔴 必须处理）

**1. SQL 注入 / 危险操作**

DuckDB 支持 `COPY TO`、`CREATE TABLE`、写文件等操作。Agent 生成的 SQL 如果包含写操作会造成安全风险。

- data_query 内部查询模式只允许 SELECT 语句（关键词检查）
- 导出模式由平台拼接 COPY TO 语句，不允许 Agent 直接写 COPY
- DuckDB 安全配置三件套：`allowed_directories` + `enable_external_access=false` + `lock_configuration=true`
- 目录级白名单只开放用户 workspace（含 staging + 下载 子目录）

**11. 文件路径穿越**

Agent 生成 `file="../../etc/passwd"` 或跨用户路径，读到不该读的文件。

- 复用现有 `FileExecutor.resolve_safe_path()` 安全检查
- 校验路径必须在当前用户的 workspace 或 staging 目录内
- 拒绝符号链接穿越

**12. 同组织跨用户文件访问**

同一个 org 下不同用户的 staging 目录结构相近，路径校验如果只检查 org 前缀可能越权。

- 路径校验精确到 user_id 级别：`org/{org_id}/{user_id}/staging/`
- 禁止跨 user_id 目录访问

### 数据一致性（🟡 中）

**3. Excel 多 Sheet**

用户的 Excel 可能有多个 Sheet，默认只转第一个可能不是用户要的。

- 默认转第一个 Sheet
- data_query 增加可选参数 `sheet`（Sheet 名称或索引）
- 探索模式返回所有 Sheet 名称列表，让 Agent 知道有哪些 Sheet
- 不同 Sheet 缓存为独立文件：`_cache_{hash}_sheet1.parquet`

**4. 用户更新了同名文件**

用户先上传 `销售报表.xlsx`，缓存了 Parquet。然后上传新版本，缓存过期但未感知。

- 缓存时记录源文件的 **(mtime, size)** 快照
- 下次引用时比对源文件当前的 mtime 和 size
- 不一致 → 删除旧缓存 → 重新转换

**5. 文件名冲突**

同一对话中两个不同路径下的同名文件，缓存会互相覆盖。

- 缓存命名加路径哈希：`_cache_{md5(full_path)[:8]}_{filename}.parquet`
- 保证不同路径的同名文件有不同的缓存

### 并发类（🟡 中）

**13. Excel 转换竞态**

两个并行的 data_query 同时引用同一个 Excel 文件，都发现没有缓存，同时开始转换，互相覆盖。

- 转换前用文件锁（`asyncio.Lock` 按文件路径隔离）
- 拿到锁后再检查一次缓存是否已存在——双重检查锁模式
- 第二个调用发现缓存已存在 → 直接使用，跳过转换

**14. data_query 查询超时**

Agent 写了复杂 SQL（多层嵌套、窗口函数 on 百万行），DuckDB 执行超预期。

- DuckDB 设置查询超时：30 秒
- 超时后返回错误：`"查询超时（30s），请简化 SQL 或缩小数据范围"`

**15. DuckDB 内存限制**

DuckDB 虽然内存效率高，但复杂 JOIN 或大聚合仍可能占用大量内存。

- DuckDB 连接时设置 `memory_limit='256MB'`
- 超出时 DuckDB 自动溢出到磁盘（DuckDB 原生支持）

### 可靠性（🟡 中）

**6. Parquet 写入中断**

Excel → Parquet 转换过程中服务崩溃，留下不完整的 Parquet 文件。

- 写入时先写临时文件（`_tmp_{uuid}.parquet`）
- 完成后 `os.rename()` 原子操作替换为正式文件名
- 启动时清理 `_tmp_` 前缀的残留文件

**7. Registry 进程重启后丢失**

session_file_registry 在内存中，服务重启后全部丢失。

- 可接受：兜底逻辑（data_query 重查 schema）覆盖此场景
- 所有文件在重启后都变成"孤儿文件"，24h 后按 TTL 清理
- 如果重启频繁影响体验，后续考虑持久化 registry 到 DB（当前不做）

**8. CSV 编码问题**

中文 CSV 常见 GBK 编码，DuckDB 默认 UTF-8 读取会乱码。

- data_query 内部先用 chardet/cchardet 检测编码
- 非 UTF-8 时转换后再给 DuckDB
- 或使用 DuckDB 的 `encoding` 参数：`read_csv('file.csv', encoding='GBK')`

### 结果处理（🟡 中）

**16. 空结果误判**

SQL 返回 0 行，Agent 可能误判为"查询失败"然后重试或换 SQL。

- 返回信息明确区分：`"查询成功，结果为空（0 行匹配条件）。"`
- 不要返回空字符串——空结果是合法结果，不是错误

**17. SQL 错误信息质量**

Agent 写了错误的 SQL（列名拼错、语法错误），DuckDB 原始异常信息不友好。

- 捕获 DuckDB 异常，格式化为 Agent 可理解的信息
- 附上可用列名列表帮助 Agent 修正：
  `"SQL 错误：列 '店铺名' 不存在。可用列：店铺名称, 金额, 日期, ..."`

### 工程类（🟢 低）

**18. 文件类型检测**

data_query 靠扩展名判断文件格式，扩展名错误或缺失会走错分支。

- 扩展名 + magic bytes 双重检测
- Parquet 文件头 `PAR1`，Excel 文件头 `PK`（zip 格式），CSV 无固定头则 fallback 文本处理

**19. 复用已有 DuckDB 引擎**

项目 `data_profile.py` 已有 `build_profile_from_duckdb()`，DuckDB 引擎已在使用。

- 建一个 `DuckDBEngine` 工厂或单例
- data_query 和 data_profile 共用，避免重复初始化

### 使用约定（🟢 低）

**9. SQL 中的表名统一**

Agent 写 SQL 时不需要知道文件路径，统一使用 `FROM data` 作为表名。

- 使用 DuckDB `CREATE TEMP VIEW data AS SELECT * FROM read_parquet('...')`
- SQL 解析器天然区分表名 `data` 和列名 `data_type`，零歧义
- TEMP VIEW 不加载数据到内存，按需从磁盘读取
- 不做字符串替换（会误伤 `data_type`/`data_source` 等列名）

**10. 中文列名引号**

DuckDB 查中文列名必须用双引号（`SELECT "店铺名称" FROM data`），Agent 可能遗漏。

- 不做自动补引号（风险太高，容易误伤字符串值和别名）
- 提示词中写明：中文列名用双引号包裹
- SQL 报错时返回可用列名列表，Agent 自行修正
- 这是 text-to-SQL 行业标准模式：schema 引导 → 生成 SQL → 失败返回列名 → LLM 自动修正

### 关键设计决策

**20. 多文件 JOIN → 不支持，单文件 SQL + 并行调用**

行业标准：Vanna.ai、DuckDB Chat、WrenAI 全都是单文件/单表查询。

- data_query 只做单文件查询，`FROM data` 不变
- 多文件场景通过并行调用 + code_execute 合并解决：
  ```
  并行：
    data_query(file=A, sql="SELECT shop, SUM(amount) GROUP BY shop") → 5 行
    data_query(file=B, sql="SELECT shop, count(*) GROUP BY shop")   → 5 行
  顺序：
    code_execute：合并两个小结果 → 计算 → 出报表
  ```
- 每个 data_query 通过 SQL 聚合把大数据变小，code_execute 只合并小数据
- 与主 Agent 提示词的"任务拆分 + 并行调用 + 编排串联"完全配合

**21. 结果返回策略 → 四档分层（与边缘情况 #2 统一）**

对齐 Claude Code 模式（输出超阈值自动持久化）+ 行业调研分档。

| 行数 | 返回 | 写 staging？ |
|------|------|-------------|
| ≤10 行 | 完整 Markdown 表格 | 否 |
| 11-100 行 | 完整 Markdown 表格 + 统计摘要 | 否 |
| 101-1000 行 | 统计摘要 + 前5行预览 | 是 |
| >1000 行 | 统计摘要 + 前5行预览 + 提示缩小范围 | 是 |

- Agent 无需感知分档——平台自动处理
- 写 staging 的是 SQL 过滤后的子集，不是原始大表
- code_execute 读取这些子集完全安全

### 完整汇总

| # | 类别 | 问题 | 严重度 | 方案 |
|---|------|------|--------|------|
| 1 | 安全 | SQL 写操作 | 🔴 高 | SELECT 白名单 + allowed_directories + lock_configuration |
| 2 | 安全 | 结果分档 | 🔴 高 | ≤10完整/11-100完整+摘要/101+摘要+staging |
| 11 | 安全 | 路径穿越 | 🔴 高 | 复用 resolve_safe_path 校验 |
| 12 | 安全 | 跨用户访问 | 🔴 高 | 路径校验精确到 user_id |
| 20 | 设计 | 多文件 JOIN | 🟢 不支持 | 单文件 SQL + 并行调用 + code_execute 合并 |
| 21 | 设计 | 结果返回策略 | 🟢 已定 | 四档分层（10/100/1000 行阈值）|
| 3 | 一致性 | Excel 多 Sheet | 🟡 中 | 默认第一个 + sheet 参数 + 列出 Sheet |
| 4 | 一致性 | 同名文件更新 | 🟡 中 | (mtime, size) 快照校验 |
| 5 | 一致性 | 文件名冲突 | 🟡 中 | 缓存名加路径哈希 |
| 13 | 并发 | Excel 转换竞态 | 🟡 中 | 双重检查锁 |
| 14 | 并发 | 查询超时 | 🟡 中 | DuckDB 30s 超时 |
| 15 | 并发 | 内存限制 | 🟡 中 | memory_limit=256MB + 磁盘溢出 |
| 6 | 可靠性 | 写入中断 | 🟡 中 | 临时文件 + rename 原子操作 |
| 7 | 可靠性 | Registry 重启丢失 | 🟡 中 | 兜底重查，暂不持久化 |
| 8 | 可靠性 | CSV 编码 | 🟡 中 | chardet 检测 + encoding 参数 |
| 16 | 结果 | 空结果误判 | 🟡 中 | 明确返回"查询成功，0 行" |
| 17 | 结果 | SQL 错误信息 | 🟡 中 | 格式化错误 + 附可用列名 |
| 18 | 工程 | 文件类型检测 | 🟢 低 | 扩展名 + magic bytes |
| 19 | 工程 | DuckDB 引擎复用 | 🟢 低 | 共用 DuckDBEngine 工厂 |
| 9 | 约定 | SQL 表名 | 🟢 低 | CREATE TEMP VIEW data（零歧义）|
| 10 | 约定 | 中文列名 | 🟢 低 | 提示词引导 + 错误重试附列名 |

## 十一、外部影响分析

### 部署策略：提示词 + 代码同时上线，无过渡期

所有提示词（TOOL_SYSTEM_PROMPT / CODE_ROUTING_PROMPT / BASE_AGENT_PROMPT / data_profile [读取]行）
与 data_query 代码同时部署。Agent 立刻按新流程走，不存在新旧混用。

### 受影响的模块

| 模块 | 影响 | 风险 |
|------|------|------|
| erp_agent 工具链 | phase_tools.py 加 data_query + BASE_AGENT_PROMPT 更新 | 🟡 需回归测试 |
| sandbox/_auto_upload | 提取为公共模块，data_query + code_execute 共用 | 🟡 提取后现有测试必须全绿 |
| session_file_registry | 扩展存 schema + embedding，from_snapshot 需向后兼容 | 🔴 新字段设默认值 |
| context_compressor | 压缩后文件引用格式需和 data_query file 参数兼容 | 🟡 确认格式一致 |
| DuckDB spatial 扩展 | 生产环境需预装（Dockerfile 加入） | 🟡 提前准备 |
| 前端 | 无影响（[FILE] 标签格式不变） | 🟢 |

### 性能影响

| 维度 | 增量 |
|------|------|
| 每次消息 embedding 调用 | +20-50ms |
| DuckDB 连接创建/销毁 | +5ms/次 |
| Excel 首次转 Parquet | +1-5s（一次性） |
| 总体 | 每次消息 ~50ms，用户无感知 |

### 测试影响

需更新：test_chat_tools / test_code_tools / test_erp_agent / test_chat_context
需新增：test_data_query / test_schema_filter / test_staging_cleanup

## 十二、提示词变更清单

需要同时修改的提示词（与代码同时部署）：

### 1. TOOL_SYSTEM_PROMPT — code_execute 段（chat_tools.py）

```
### code_execute — 计算与文件生成

对数据做计算、可视化、格式转换，生成报表和图表。

何时使用：拿到查询结果（上下文中的小数据）后，需要计算涨跌幅、画趋势图、
生成 Excel 报表时使用。

核心能力：
- 可用库：pd, plt, Path, math, json, datetime, Decimal, Counter, io
- 生成的文件写到 OUTPUT_DIR，平台自动检测上传
- 图表用 plt.savefig(OUTPUT_DIR + '/图.png', dpi=150, bbox_inches='tight')
- 写 Excel 用 engine='xlsxwriter'
- 每次执行都是全新子进程，不保留任何变量
- 用 print() 输出文本结果

注意事项：
- 不要用 code_execute 读取大数据文件——大文件用 data_query 查询
- 禁止 import os/sys
```

### 2. TOOL_SYSTEM_PROMPT — 新增 data_query 段（chat_tools.py）

```
### data_query — 数据查询与导出

查询 staging 文件或工作区数据文件的内容，支持探索结构、SQL 查询和文件导出。

何时使用：
- 收到 staging 文件引用后，需要从中提取特定数据时
- 需要了解一个数据文件有哪些列、多少行时
- 需要将查询结果直接导出为 Excel 时

核心能力：
- file 传文件名（如 "trade_123.parquet" 或 "销售报表.xlsx"）
- 不传 sql：返回文件结构（列名、类型、行数、统计信息）
- 传 sql：执行查询，表名统一用 FROM data
- 传 export：直接生成导出文件（如 export="月度报表.xlsx"）

参数：
- file：文件名或相对路径（必填）
- sql：SQL 查询语句，表名用 FROM data（可选）
- export：导出文件名，传则生成文件而非返回数据（可选）
- sheet：Excel 的 Sheet 名称（可选，默认第一个）

注意事项：
- 中文列名必须用双引号包裹：SELECT "店铺名称" FROM data
- SQL 出错时会返回可用列名列表，据此修正后重试
- 分析大数据用 SQL 聚合筛选，不要 SELECT * 全量取出
- 只支持单文件查询，多文件对比用多次并行调用分别聚合后合并
```

### 3a. _DESCRIPTION_WORKSPACE — 主 Agent code_execute 工具定义（code_tools.py）

只改 2 行，保留所有现有能力：

```
在沙盒子进程中执行 Python 代码并返回输出。

工作目录为用户工作区，直接用文件元信息中的路径读取文件。
每次执行都是全新子进程，不保留任何变量。

可用库: pd, plt, Path, math, json, datetime, Decimal, Counter, io
环境变量: STAGING_DIR（staging 数据）、OUTPUT_DIR（输出目录，自动上传）
读 Excel 用 engine='calamine'，写 Excel 用 engine='xlsxwriter'。
大数据文件用 data_query 查询提取所需子集。data_query 暂存的小数据集可用 pd.read_parquet() 读取。
生成的文件写到 OUTPUT_DIR，用 print() 输出文本。
禁止 import os/sys。
```

差异：
- 删：`ERP 数据由 erp_agent 查询，结果以 Parquet 格式存入 STAGING_DIR。`
- 改：`读 staging 数据用 pd.read_parquet()。` → `大数据文件用 data_query 查询提取所需子集。data_query 暂存的小数据集可用 pd.read_parquet() 读取。`

### 3b. _DESCRIPTION_BASE — ERP Agent code_execute 工具定义（code_tools.py）

同样只改 2 行：

```
在沙盒子进程中执行 Python 代码并返回输出。

沙盒内不能查询数据，大数据用 data_query 查询提取所需子集。
每次执行都是全新子进程，不保留任何变量。

可用库: pd, plt, Path, math, json, datetime, Decimal, Counter, io
环境变量: STAGING_DIR（staging 数据）、OUTPUT_DIR（输出目录，自动上传）
data_query 暂存的小数据集可用 pd.read_parquet() 读取。
写 Excel 用 engine='xlsxwriter'。
生成的文件写到 OUTPUT_DIR，用 print() 输出文本。
禁止 import os/sys。
```

差异：
- 改：`沙盒内不能查询数据，数据由其他工具获取后存入 STAGING_DIR。` → `沙盒内不能查询数据，大数据用 data_query 查询提取所需子集。`
- 改：`读 staging 数据用 pd.read_parquet()。` → `data_query 暂存的小数据集可用 pd.read_parquet() 读取。`

### 4. CODE_ROUTING_PROMPT — ERP Agent 使用协议（code_tools.py）

保留骨架，更新工具列表和典型流程，新增 data_query 协议：

```
## code_execute 使用协议
- code_execute 是计算沙盒，只能处理已获取的数据，不能查询数据
- 数据获取必须先通过工具层完成（local_db_export / fetch_all_pages / data_query），
  大数据用 data_query SQL 查询提取所需子集
- data_query 暂存的小数据集可在沙盒内用 pd.read_parquet(STAGING_DIR + '/文件名') 读取
- 生成文件写到 OUTPUT_DIR 目录，平台自动检测上传，不需要手动上传
- 图表用 plt.savefig(OUTPUT_DIR + '/图.png', dpi=150, bbox_inches='tight');
  plt.close() 释放内存
- 典型流程：local_db_export → data_query SQL 提取 → code_execute 计算 →
  df.to_excel(OUTPUT_DIR + '/报表.xlsx')
- 顶层可直接 await，用 print() 输出文字

## data_query 使用协议
- 查询 staging 文件或工作区数据文件，file 传文件名，sql 中表名用 FROM data
- 不传 sql 返回文件结构信息（列名、类型、统计）
- 传 export 直接生成导出文件到 OUTPUT_DIR
- 中文列名用双引号包裹
- 分析大数据用 SQL 聚合筛选，不要 SELECT * 全量取出

## fetch_all_pages 使用协议
- 全量翻页工具，包装任意 erp_* 远程查询工具，自动翻页拉全部数据
- 仅用于本地数据库没有的数据（如物流轨迹），本地有的数据用 local_db_export
- 结果自动存 staging 文件（Parquet），返回文件路径
- 使用前需先通过 erp_* 工具的两步协议确认参数格式
```

### 5. BASE_AGENT_PROMPT — 大数据处理规则（phase_tools.py）

```
## 大数据处理规则
- 当工具返回 <persisted-output> 标签或 staging 文件引用时，说明数据量过大已存入文件
- 用 data_query 查询文件内容：先不传 sql 了解文件结构，再用 SQL 提取所需数据
- 禁止直接使用 Preview 中的数据回答用户，Preview 仅供了解数据结构
- 数据量大时用 data_query(export="报表.xlsx") 直接导出，或用 SQL 聚合后交 code_execute 生成图表
```

### 6. data_profile.py — [读取] 行（两个函数都改）

`build_data_profile()` 和 `build_profile_from_duckdb()` 各改 1 行：

```python
# 旧
lines.append(f"\n[读取] df = pd.read_parquet(STAGING_DIR + '/{filename}')")
# 新
lines.append(f'\n[查询] data_query(file="{filename}", sql="SELECT ... FROM data")')
```

### 提示词变更总览

| # | 位置 | 改动量 | 风险 |
|---|------|--------|------|
| 1 | TOOL_SYSTEM_PROMPT code_execute 段 | 重写为 5 板块 | 🟢 低 |
| 2 | TOOL_SYSTEM_PROMPT 新增 data_query 段 | 新增 5 板块 | 🟢 低 |
| 3a | _DESCRIPTION_WORKSPACE（主 Agent）| 只改 2 行 | 🟢 低 |
| 3b | _DESCRIPTION_BASE（ERP Agent）| 只改 2 行 | 🟢 低 |
| 4 | CODE_ROUTING_PROMPT | 保留骨架 + 更新工具/流程 + 新增 data_query | 🟡 中 |
| 5 | BASE_AGENT_PROMPT | 4 行替换（code_execute → data_query）| 🟢 低 |
| 6 | data_profile.py [读取] 行 | 两个函数各 1 行 | 🟢 低 |

## 十三、实现细节备忘

- DuckDB 连接每次 data_query 调用创建新的 `:memory:` 连接，用完销毁（避免 TEMP VIEW "data" 名称冲突）
- embedding 预计算异步执行（fire-and-forget），失败不阻塞工具返回，降级到 Qwen-Flash 兜底
- data_profile.py 的 `[读取]` 行改为 `[查询] data_query(file="...", sql="SELECT ... FROM data")`
- `allowed_directories` 设为 `[workspace_dir]`（覆盖 staging + 下载 子目录 + 工作区根目录的 CSV）

## 十四、待做

### Phase 1：data_query 核心
- [ ] data_query 工具实现（DuckDB 引擎 + 探索/查询/导出三模式）
- [ ] DuckDB 安全连接（allowed_directories + enable_external_access=false + lock_configuration=true）
- [ ] TEMP VIEW 表别名（CREATE TEMP VIEW data AS SELECT * FROM read_parquet(...)）
- [ ] DuckDB spatial 扩展集成（COPY TO xlsx 导出能力）
- [ ] DuckDBEngine 工厂/单例（复用已有 duckdb_engine.py）
- [ ] Excel → Parquet 缓存转换逻辑（calamine + 双重检查锁 + mtime 校验）
- [ ] 安全层（SELECT 白名单 + 路径校验 + 目录白名单）
- [ ] 结果分档返回（≤10完整表格 / 11-100完整+摘要 / 101+摘要+staging）
- [ ] auto_upload 公共化（从 sandbox/functions.py 提取，data_query + code_execute 共用）

### Phase 2：schema 持久化 + 智能过滤注入
- [ ] session_file_registry 扩展（存 data_profile schema + 预计算 embedding 向量）
- [ ] schema 过滤模块（embedding 相似度 + Qwen-Flash 降级，复用 memory_filter 架构）
- [ ] 上下文构建逻辑（每轮消息调过滤模块 → 注入相关 schema）
- [ ] 压缩逻辑调整（正常压缩 schema，不特殊保留）
- [ ] text-embedding-v3 接入（DashScope，和 memory_filter 共用 client）

### Phase 3：staging 清理
- [ ] registry 保护伞清理逻辑（保护 registry 中的文件 + 孤儿文件 24h TTL）
- [ ] LRU 淘汰（registry 上限 20 条）+ 容量兜底（500MB）
- [ ] 进程启动兜底扫描

### Phase 4：提示词与工具描述
- [ ] code_execute 工具描述重写（纯计算引擎，删除读 staging 指引）
- [ ] TOOL_SYSTEM_PROMPT 新增 data_query 使用协议（FROM data + 中文列名双引号 + 分档说明）
- [ ] CODE_ROUTING_PROMPT 同步更新（ERP Agent 也用 data_query）
- [ ] BASE_AGENT_PROMPT 大数据处理规则更新（data_query 替代 code_execute 读数据）
- [ ] data_profile.py `[读取]` 行改为 `[查询] data_query(file=..., sql=...)`
- [ ] phase_tools.py 工具列表加 data_query（ERP Agent 两层分离）

### Phase 5：测试验证
- [ ] 单元测试（探索/查询/导出三模式 + 边缘情况 21 项）
- [ ] 集成测试（ERP 数据分析 + 工作区文件分析 + 全量导出）
- [ ] 企微长对话压力测试（staging 清理 + schema 恢复）

# data_query 工具设计方案

> 版本：v1.0 | 日期：2026-04-30 | 状态：方案讨论完成，待技术设计

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
data_query(file: str, sql: str = None)
```

- **file**：文件路径（staging 相对路径或工作区文件名）
- **sql**：SQL 查询语句（可选）

### 两种模式

| 模式 | 触发条件 | 行为 | 返回 |
|------|---------|------|------|
| 探索模式 | 不传 sql | DuckDB 读文件 metadata | data_profile（列名、类型、行数、统计、预览） |
| 查询模式 | 传 sql | DuckDB 执行 SQL | 查询结果（小结果直接返回，大结果写 staging + 摘要） |

### 覆盖两个数据源

| 数据源 | 文件格式 | DuckDB 处理方式 |
|--------|---------|----------------|
| staging（erp_agent 产出） | Parquet | 直接查询 |
| 工作区（用户上传） | Excel / CSV / Parquet | CSV/Parquet 直接查；Excel 先转 Parquet 缓存到 staging |

### Excel 转换缓存

- 第一次 data_query 引用 Excel 文件时，用 calamine 引擎读取 → 写 Parquet 到 staging
- 缓存文件命名：`_cache_{原始文件名}.parquet`
- 同一对话内后续查询直接走缓存，不重复转换
- 转换后 df 立即释放内存，后续查询全走 DuckDB 磁盘读取

## 三、存储位置

```
workspace/
├── 销售报表.xlsx                ← 用户原始文件（不动）
├── staging/{conv_id}/
│   ├── trade_123.parquet        ← erp_agent 产出的数据
│   └── _cache_销售报表.parquet   ← Excel 转换缓存（data_query 自动生成）
└── 下载/
    └── 分析报告.xlsx             ← code_execute 输出的最终结果
```

## 四、工具协作分工

| 工具 | 职责 | 何时用 |
|------|------|--------|
| erp_agent | 查 ERP 数据 | 需要业务数据时 |
| data_query | 从大文件精确提取数据（SQL） | 拿到 staging 引用或工作区大文件时 |
| code_execute | 计算和生成（涨跌幅、图表、Excel 报表） | 拿到小数据后做计算/可视化 |
| file_read | 读文本/PDF/图片 | 非数据文件 |

### 典型流程

**ERP 数据分析：**
1. erp_agent → 大数据写 staging → 返回 data_profile
2. data_query(sql) → DuckDB 精确提取需要的子集 → 返回小结果
3. code_execute → 用小结果计算/画图/出报表

**工作区文件分析：**
1. data_query(file="报表.xlsx") → 探索模式，转 Parquet 缓存 + 返回 schema
2. data_query(file="报表.xlsx", sql="SELECT ...") → 查询模式，精确提取
3. code_execute → 计算/出报表

## 五、schema 持久化（两层设计）

### 核心问题

data_profile 约 400-600 tokens/个。如果始终保留在对话上下文中，10 个文件就占 4000-6000 tokens，
对话越长占比越大，挤压其他内容的空间。

### 方案：外部索引 + 按需注入 + 兜底重查

```
schema 完整生命周期：

创建：
    data_query 返回 schema
    ├─ 进入对话上下文（当轮可用）
    └─ 存入 session_file_registry（对话级外部索引）

使用：
    Agent 引用文件 → 平台从 registry 取 schema → 注入当前轮
    只注入被引用文件的 schema，不注入无关文件

压缩：
    上下文中的 schema 随普通工具结果正常压缩（只保留文件路径引用）
    registry 中的 schema 不受影响 → 下次引用时按需注入

清理（schema 跟着文件走，文件没了 schema 也没了）：
    ├─ staging 文件被清理时 → 同步删除 registry 中对应的 schema
    ├─ 对话结束时 → 整个 session_file_registry 随对话销毁
    └─ Excel 缓存（_cache_*.parquet）→ 随 staging 一起清理

兜底：
    registry 中查不到 schema（异常） → Agent 调 data_query 重查
    DuckDB 读 Parquet metadata，毫秒级返回
```

### 优势

- 上下文不会被 schema 持续占用——用多少注入多少
- 压缩逻辑不需要特殊处理——正常压缩即可
- 文件清理时 schema 同步清理——不留垃圾
- 兜底逻辑保证任何情况都能恢复 schema

### 需要修改

1. **session_file_registry.py**：扩展存储 data_profile schema（列名、类型、统计、行数）
2. **上下文构建逻辑**：检测当前轮次引用的文件 → 从 registry 注入对应 schema
3. **压缩逻辑**：正常压缩 schema，只保留文件路径引用
4. **staging 清理逻辑**：文件删除时同步清理 registry 中对应的 schema 条目

## 六、已完成的前置改动

### 主 Agent 提示词优化（2026-04-30 已上线）

commit: `bac5ca0`

TOOL_SYSTEM_PROMPT 新增三个段落：
- **任务拆分**：复杂请求拆最小独立子任务，禁止打包
- **并行与顺序**：独立子任务并行，有依赖顺序执行
- **编排与串联**：以终为始规划每步输入输出，staging 中转 + print 摘要

## 七、大厂方案调研（2026-04-30 完成）

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

## 八、staging 清理策略（企微长对话场景）

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

## 九、边缘情况与防御设计

### 安全类（🔴 必须处理）

**1. SQL 注入 / 危险操作**

DuckDB 支持 `COPY TO`、`CREATE TABLE`、写文件等操作。Agent 生成的 SQL 如果包含写操作会造成安全风险。

- data_query 内部只允许 SELECT 语句
- 解析 SQL 前做关键词检查：拒绝 INSERT/UPDATE/DELETE/DROP/CREATE/COPY/ATTACH 等
- DuckDB 连接使用只读模式（`read_only=True`）

**2. 查询结果无上限**

Agent 写 `SELECT * FROM data` 不加 LIMIT，大表全量返回会爆上下文。

- 强制结果行数上限：1000 行
- 超过时自动写 staging + 返回 data_profile 摘要
- 返回信息中提示 Agent："结果已截断，共 N 行，已存入 staging，可用更精确的 SQL 缩小范围"

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

- data_query 内部将 `FROM data` 替换为实际文件路径 `FROM 'staging/.../file.parquet'`
- Agent 只需关注列名和查询逻辑

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

**21. 结果返回策略 → 固定 1000 行上下文 + 超限自动写 staging**

对齐 Claude Code 模式（Bash 输出超 30K 字符自动持久化）。

- 结果 ≤ 1000 行 → 直接返回上下文
- 结果 > 1000 行 → 完整结果写 staging Parquet + 返回摘要：
  ```
  "查询返回 8,234 行，已存入 staging。
   前 5 行预览：...
   如需全部数据请用 code_execute 读取，
   或用更精确的 SQL 缩小范围。"
  ```
- Agent 无需感知 LIMIT 的存在——平台自动处理
- 需要全量数据时：code_execute 读 staging（此时是 SQL 过滤后的子集，不是原始大表）

### 完整汇总

| # | 类别 | 问题 | 严重度 | 方案 |
|---|------|------|--------|------|
| 1 | 安全 | SQL 写操作 | 🔴 高 | 只允许 SELECT + read_only 连接 |
| 2 | 安全 | 结果无上限 | 🔴 高 | 固定 1000 行上下文 + 超限写 staging |
| 11 | 安全 | 路径穿越 | 🔴 高 | 复用 resolve_safe_path 校验 |
| 12 | 安全 | 跨用户访问 | 🔴 高 | 路径校验精确到 user_id |
| 20 | 设计 | 多文件 JOIN | 🟢 不支持 | 单文件 SQL + 并行调用 + code_execute 合并 |
| 21 | 设计 | 结果返回上限 | 🟢 已定 | 1000 行上下文 + 超限自动写 staging |
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
| 9 | 约定 | SQL 表名 | 🟢 低 | 统一 FROM data，内部替换 |
| 10 | 约定 | 中文列名 | 🟢 低 | 提示词引导 + 错误重试附列名 |

## 十、待做

- [ ] data_query 工具实现（DuckDB 引擎 + 探索/查询双模式）
- [ ] session_file_registry 扩展（存 schema + 按需注入）
- [ ] Excel → Parquet 缓存转换逻辑
- [ ] code_execute 工具描述更新（STAGING_DIR 读写）
- [ ] CODE_ROUTING_PROMPT 同步更新（ERP Agent）
- [ ] 主 Agent 提示词更新（data_query 使用协议）
- [ ] 压缩逻辑调整（正常压缩 schema，不特殊保留）
- [ ] 测试验证

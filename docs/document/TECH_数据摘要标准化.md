# 技术设计：数据摘要标准化 + Detail 模式 Staging 修复

> 版本：v1.0 | 日期：2026-04-19 | 等级：A级

## 背景

重构 ERPAgent 时删除了 `ComputeAgent` / `DAGExecutor` 中间层，但 `_query_local_data()` 直接返回 `UnifiedQueryEngine` 的结果，绕过了 `_build_output()` 的 staging 分流逻辑。导致 detail 模式返回 200 行原始数据，`to_message_content()` JSON dump 全部行进 LLM 上下文，token 爆炸（12 万 tokens）。

**参考**：Anthropic 实测——只返回高信号摘要给 LLM，token 消耗减少 37%，准确率更高。OpenAI Code Interpreter / LangChain `content_and_artifact` 均采用"数据走文件，LLM 只看摘要"模式。

---

## 1. 项目上下文

- **架构现状**：ERP 数据查询通过 `ERPAgent → DepartmentAgent → UnifiedQueryEngine` 三层调用。`_build_output()` 和 `_write_to_staging()` 已有完整的 staging 分流逻辑，但 `_query_local_data()` 绕过了它。
- **可复用模块**：`_build_output()`、`_write_to_staging()`、`FileRef`、`ColumnMeta` 全部可复用；`_fetch_all_pages()` 是正确的参考实现
- **设计约束**：必须兼容 `ToolOutput` 协议层；staging 目录用户级隔离；沙盒通过 `STAGING_DIR` 访问
- **潜在冲突**：无

---

## 2. 核心设计

### 2.1 原则：数据走文件，LLM 只看摘要

```
summary 模式 → TABLE（inline JSON）  → ✅ 不变（统计数据本来就小）
detail 模式  → FILE_REF（staging）    → ✅ LLM 只看 profile 摘要
```

detail 模式**统一走 FILE_REF**，不区分数据大小，不走 TABLE。TABLE 格式保留给 summary 的聚合统计数据。

### 2.2 数据摘要 7 个板块（Anthropic 标准）

```
[数据已暂存] staging/trade_1713520081.parquet
共 2,013 条 | 8 列 | 2048KB | 耗时 3.2s

[字段] order_no(text) | shop_name(text) | amount(numeric) | 
       pay_time(timestamp) | platform(text) | status(text) | 
       product_code(text) | qty(integer)

[质量] 空值: amount=3条(0.1%), pay_time=2条(0.1%) | 重复: 0条

[统计] amount: 合计¥89,234.50 最小¥12.00 最大¥3,200 均值¥71.55
       qty: 合计12,500 最小1 最大200 均值6.2

[预览] 前3条:
  1. order_no=12345 | shop=旗舰店 | amount=99.9 | pay_time=04-18 23:59
  2. order_no=12346 | shop=专卖店 | amount=149.9 | pay_time=04-18 23:50
  3. order_no=12347 | shop=官方店 | amount=199.9 | pay_time=04-18 23:45

[读取] df = pd.read_parquet(STAGING_DIR + '/trade_1713520081.parquet')

⚠ 数据最后同步于 5 分钟前 | 仅含已同步数据
```

### 2.3 `build_data_profile()` 纯函数设计

```python
def build_data_profile(
    df: pd.DataFrame,
    source: str,           # "erp" / "workspace"
    filename: str,         # staging 文件名（不含路径）
    file_size_kb: float,
    elapsed: float = 0,
    sync_info: str = "",   # 同步健康信息
) -> str:
    """生成标准数据摘要。~300-500 tokens。纯函数，零副作用。"""
```

- 元信息：`df.shape` + file_size + elapsed
- 字段：`df.dtypes` 映射到 text/integer/numeric/timestamp
- 质量：`df.isnull().sum()` 筛出 >0 的列 + `df.duplicated().sum()`
- 统计：数值列（int/float）的 sum/min/max/mean
- 预览：`df.head(3)` 格式化为 `key=value` 紧凑格式
- 读取：`pd.read_parquet(STAGING_DIR + '/filename')`
- 警告：sync_info + 空值提醒 + 截断提醒

### 2.4 `to_message_content()` FILE_REF 路径隐藏

当前第 145 行 `path: {self.file_ref.path}` 暴露了 `/mnt/oss-workspace/...` 绝对路径。

修复：输出相对路径 `STAGING_DIR + '/filename'`，与 profile 的 [读取] 指引一致。

```python
# 修改前
tag_lines.append(f"path: {self.file_ref.path}")

# 修改后
tag_lines.append(f"path: STAGING_DIR + '/{self.file_ref.filename}'")
```

### 2.5 ERPAgent 执行流（关键发现：不走 ToolLoopExecutor）

ERPAgent **不使用 ToolLoopExecutor**，有自己的执行流：

```
ERPAgent.execute(query)
  → _llm_extract(query)          # 提取 domain + params
  → _create_agent(domain)        # 构造 DepartmentAgent（当前只传 db/org_id/request_ctx）
  → agent.execute(task, params)  # DepartmentAgent.execute()
    → _dispatch(action, params)
      → _query_local_data()
        → UnifiedQueryEngine.execute()
        → 返回 ToolOutput
  → _build_result(result, ...)   # 只取 result.summary → ERPAgentResult(text=summary)
```

**关键点**：
- ERPAgent 只用 `result.summary`，**不调 `to_message_content()`**
- 所以 profile 文本必须在 `summary` 字段里（不是 [DATA_REF] 标签里）
- `_build_result()` 返回 `ERPAgentResult(text=summary)`，主 agent 看到的就是这段文字
- staging_dir 必须与主 agent 的 `code_execute` 用的同一个目录（同 user_id + org_id + conv_id → 同目录 ✅）

### 2.6 staging_dir 传递链路

```
erp_agent.py
  ├─ resolve_staging_dir(root, user_id, org_id, conv_id)
  ├─ _create_agent(domain) 增加 staging_dir 参数
  │   └─ cls(db=self.db, org_id=self.org_id, request_ctx=..., staging_dir=xxx)
  └─ DepartmentAgent.__init__ 新增 staging_dir 参数
       └─ self._staging_dir = staging_dir
            └─ _query_local_data() detail 模式时传给 _build_output(staging_dir=self._staging_dir)
```

### 2.7 detail 模式 ToolOutput.summary 内容变更

修改前（token 爆炸）：
```
summary = fmt_detail_rows(rows, ...)  # 200 行完整格式化文本（~20K 字符）
data = rows                            # 200 行原始数据
format = TABLE                         # to_message_content() JSON dump 全部
```

修改后（profile 摘要）：
```
summary = build_data_profile(df, ...)  # ~500 字符的 7 板块摘要
data = None                            # 数据在 staging parquet 里
file_ref = FileRef(...)                # 指向 staging 文件
format = FILE_REF                      # to_message_content() 只显示摘要 + 路径
```

ERPAgent._build_result() 取 result.summary → 主 agent 看到的是 profile 摘要 → 主 agent 调 code_execute 读 staging parquet → 导出 Excel。

---

## 3. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 查询结果 0 条 | 返回 EMPTY 状态 + 同步健康检查（已有），不走 staging | `_query_local_data` |
| 查询结果 1-200 条 | 统一走 staging + profile（不区分大小） | `_query_local_data` |
| DataFrame 全列为空值 | 质量报告标注"⚠ 全列空值" | `build_data_profile` |
| 数值列全为 0 或负数 | 统计正常计算（交给 LLM 第二层检查） | `build_data_profile` |
| parquet 写入失败 | 降级为 JSON（已有逻辑） | `_write_to_staging` |
| workspace 文件不是表格 | profile 只对 DataFrame 生效，非表格跳过 | `build_data_profile` |
| staging_dir 为 None | `_build_output` 降级为 inline（已有 warning 日志） | `_build_output` |
| summary 模式 | 不变，仍返回 TABLE + inline JSON（统计数据天然小） | `_query_local_data` |
| export 模式 | 不变，`_export()` 已正确写 staging（无需修改） | `UnifiedQueryEngine` |
| ERPAgent 降级路径 | `_build_fallback_params` 时 mode=detail 也走 staging | `_query_local_data` |

---

## 4. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增 `build_data_profile()` | 新文件 `data_profile.py` | 无（新增） |
| `_query_local_data` detail 模式走 staging | `department_agent.py` | 需要 `staging_dir`，从 `__init__` 获取 |
| `DepartmentAgent.__init__` 新增 staging_dir | `department_agent.py` | 所有子类构造不受影响（默认 None） |
| `erp_agent._create_agent()` 传 staging_dir | `erp_agent.py` | resolve_staging_dir 调用 |
| `_write_to_staging` 生成 profile 文本 | `department_agent.py` | 调用 `build_data_profile` |
| `_build_output` detail 模式移除 INLINE_THRESHOLD | `department_agent.py` | detail 统一 FILE_REF，summary 保留 TABLE |
| `to_message_content()` FILE_REF 路径隐藏 | `tool_output.py` | path 改为 `STAGING_DIR + '/filename'` |
| ERPAgent._build_result() 适配 FILE_REF | `erp_agent.py` | result.summary 已是 profile 文本，无需额外改动（验证） |
| 测试更新 | 多个测试文件 | 断言适配 |

---

## 5. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|---------|---------|
| 模块边界 | 新增 `data_profile.py` 为纯工具函数，不跨模块 | 低 | 放在 `services/agent/` 下 |
| 数据流向 | 恢复旧架构的 staging 分流，不引入新流向 | 低 | 复用已有逻辑 |
| 扩展性 | profile 计算 O(n) 一次遍历，万行 <100ms | 低 | 无需优化 |
| 耦合度 | `build_data_profile` 只依赖 pandas，零耦合 | 低 | 纯函数设计 |
| 一致性 | 与 `_fetch_all_pages` staging 模式一致 | 低 | 参考其格式 |
| 可观测性 | staging 写入已有日志 | 低 | 无需新增 |
| 可回滚性 | 纯代码修改，无 DB 变更，git revert 即可 | 低 | 无 |

---

## 6. 目录结构

### 新增文件
- `backend/services/agent/data_profile.py`：数据摘要生成器（纯函数，~100行）

### 修改文件
- `backend/services/agent/department_agent.py`：`_query_local_data` 增加 staging 分流 + `_write_to_staging` 调用 profile
- `backend/services/agent/erp_agent.py`：构造 DepartmentAgent 时传入 staging_dir
- `backend/services/agent/tool_output.py`：`to_message_content()` FILE_REF 路径隐藏

---

## 7. 开发任务拆分

### Phase 1：核心——data_profile + staging 修复
- [ ] 1.1 新建 `data_profile.py`，实现 `build_data_profile()`（7 个板块）
- [ ] 1.2 `DepartmentAgent.__init__` 新增 `staging_dir` 参数
- [ ] 1.3 `erp_agent.py` 构造 DepartmentAgent 时 resolve + 传入 staging_dir
- [ ] 1.4 `_query_local_data` detail 模式结果走 `_build_output` → FILE_REF + staging
- [ ] 1.5 `_write_to_staging` 调用 `build_data_profile` 生成摘要存入 `FileRef.preview`
- [ ] 1.6 `to_message_content()` FILE_REF 路径改为 `STAGING_DIR + '/filename'`
- [ ] 1.7 测试：detail 查询 → 验证 staging parquet + profile 格式 + token 消耗下降

### Phase 2：workspace 文件读取增加 profile（独立）
- [ ] 2.1 `code_execute` 读 workspace 表格文件时调用 `build_data_profile`
- [ ] 2.2 测试：上传 Excel → 读取 → 验证摘要格式

---

## 8. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| profile 计算对大 DataFrame 耗时 | 低 | O(n) 一次遍历，万行 <100ms |
| 修改 `_query_local_data` 影响现有查询 | 中 | summary/export 模式不变，只改 detail；全量测试 |
| LLM 不知道怎么读 staging 文件 | 低 | profile 里有明确的 `pd.read_parquet()` 指引 |
| ERPAgent._build_result() 只取 summary | 低 | profile 文本就在 summary 里，无需额外适配（需验证） |
| staging_dir 跨 erp_agent 和 code_execute | 低 | 同 user_id/org_id/conv_id → 同目录（已验证） |

---

## 9. 文档更新清单
- [ ] FUNCTION_INDEX.md（新增 build_data_profile）
- [ ] PROJECT_OVERVIEW.md（无文件增删目录变化则跳过）

---

## 10. 设计自检
- [x] 项目上下文已加载，4 点完整
- [x] 连锁修改已全部纳入任务拆分
- [x] 边界场景均有处理策略（含 summary/export/降级/0条 场景）
- [x] 架构影响评估无高风险项
- [x] 新增文件预估 ~100 行（`data_profile.py`）
- [x] 无新增依赖
- [x] `to_message_content()` TABLE JSON dump 问题已解决（detail 不再走 TABLE）
- [x] FILE_REF 路径隐藏已纳入
- [x] staging_dir 传递链路已明确（erp_agent → DepartmentAgent.__init__）
- [x] ERPAgent 不走 ToolLoopExecutor 已确认（只用 result.summary）
- [x] ERPAgent._build_result() 适配已验证（summary 即 profile 文本）
- [x] staging_dir 一致性已验证（erp_agent 和 code_execute 同目录）
- [x] export 模式不受影响（`_export()` 已正确处理）

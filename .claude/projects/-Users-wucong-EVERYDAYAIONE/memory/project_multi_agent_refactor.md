---
name: 多Agent单一职责重构
description: ERP Agent 拆分为部门Agent架构——V2.1第四轮评审修订，24项缺陷，Phase 0~4分阶段实施
type: project
---

## 状态：V2.1方案确认完毕，准备开工（2026-04-16）

### 技术文档
`docs/document/TECH_多Agent单一职责重构.md` — V2.1第四轮评审修订

### 经四轮架构评审确定的关键决策
- **保留ERPAgent作为路由层**：不扁平化，实测40%→92%准确率差距
- **静态DAG**：计划执行前一次性生成，不根据中间结果动态调整
- **一次性改造**：所有函数直接改返回ToolOutput，无过渡期
- **DATA_REF是LLM摘要不是数据传输协议**：Agent间传Python对象
- **产出端字段名标准化**：FIELD_MAP同步映射data key和ColumnMeta.name
- **Context注入走方式B**：Python确定性提取，零值用`is not None`保护
- **ERROR跳过/PARTIAL阈值**：flag+break防跳错层级，无total_expected则跳过阈值判断不估算
- **根因聚合**：同Round多Agent同时失败时聚合所有ERROR来源
- **allowed_doc_types**：基类_query_local_data封装+白名单校验
- **生产迁移**：Phase 0/3B低峰停服发布，pending_interaction老格式兼容，git revert回滚

### 缺陷数：24项（D1~D24）
### Phase 执行顺序
Phase 0（地基）→ Phase 1A∥1B → Phase 2 → Phase 3A∥3B → Phase 4

### 改造基线
- 当前测试：4,153 passed, 0 failed
- 涉及源文件：~8个改造 + ~15个新建
- 涉及测试文件：~11个、~120处断言更新 + ~65个新增测试

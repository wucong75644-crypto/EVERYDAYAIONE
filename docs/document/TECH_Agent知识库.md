# 技术设计：Agent 自主知识库

> 版本：v1.0 | 日期：2026-03-08
> 对应 ROADMAP：第一步 - Agent 自主知识库

---

## 1. 现有代码分析

### 已阅读文件

| 文件 | 行数 | 关键理解 |
|------|------|---------|
| memory_service.py | 516 | CRUD + 搜索 + 提取，分层架构（service → config → Mem0） |
| memory_config.py | 279 | Mem0 单例 + 异步锁初始化 + TTL 缓存 + 格式化 |
| memory_filter.py | 218 | 千问二级精排（turbo → plus → 跳过） |
| chat_handler.py | 398 | 流式生成 + 完成后两个 fire-and-forget 钩子 |
| chat_context_mixin.py | 372 | 消息组装：摘要 → 历史 → 记忆 → 搜索 → 用户消息 |
| intent_router.py | 445 | 千问 Function Calling 路由 + 重试循环 |
| image_handler.py | 412 | 异步任务 + 预扣积分，无完成后钩子 |
| video_handler.py | 343 | 同 image，无完成后钩子 |
| base.py | 419 | 基类生命周期 + 重试上下文 |
| message_mixin.py | 368 | 消息 upsert + 完成/错误回调 |
| config.py | 142 | Pydantic BaseSettings，env 驱动 |

### 可复用模块

- 记忆系统三层架构（service + config + filter）→ 镜像为 knowledge 三层
- 千问降级链模式 → 知识提取 LLM 调用
- DashScope embedding API（text-embedding-v3, 1024 维）→ 知识向量化
- pgvector 基础设施 → 知识向量存储
- fire-and-forget 模式 → 知识提取钩子

### 设计约束

- 兼容现有 Mem0 记忆系统（两套并行，互不干扰）
- 知识检索不增加路由延迟（路由阶段仅向量检索，不精排）
- 复用现有 DashScope + PostgreSQL，不引入新基础设施
- 以上约束在实际开发中如发现不合理可调整

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增指标记录钩子 | chat_handler.py | `_stream_generate` 完成后增加 `asyncio.create_task` |
| Image/Video 指标记录 | message_mixin.py | `_handle_complete_common` 末尾增加钩子 |
| 重试事件提取 | chat_handler.py, base.py | `_attempt_chat_retry` 成功后触发知识提取 |
| 路由注入知识 | intent_router.py | `_call_model` 的 system_prompt 拼接知识上下文 |
| 新增配置项 | config.py | 增加 `kb_*` 配置字段 |
| 新增迁移脚本 | migrations/ | 新建 `023_add_knowledge_base.sql` |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 知识提取 LLM 超时 | 3s 超时 + 降级链（turbo → plus → 跳过），不阻塞主流程 | knowledge_extractor |
| 重复知识 | content_hash 去重 + 向量相似度 > 0.9 更新已有条目 | knowledge_service |
| embedding API 不可用 | 跳过向量化，仅存文本（降级为分类过滤） | knowledge_config |
| 知识条目过多 | knowledge_nodes 上限 5000 条，按 confidence 淘汰最低的 | knowledge_service |
| 知识库空（冷启动） | 预置种子知识（模型能力描述），路由照常工作 | seed_knowledge |
| 并发提取（同一任务） | content_hash UNIQUE 约束防重复 | 数据库层 |
| 知识与旧知识冲突 | 更新 content + 刷新 updated_at + 重新计算 embedding | knowledge_service |
| pgvector 连接失败 | 标记 `_kb_available = False`，后续请求跳过（同 Mem0 模式） | knowledge_config |
| 指标表数据量大 | 按 created_at 分区，定期归档 90 天前的数据 | 运维层 |
| 定时聚合千问超时 | 降级链 + 跳过本次聚合，下次重试 | knowledge_aggregator |

---

## 3. 技术栈

- **后端**：Python 3.x + FastAPI（现有）
- **数据库**：Supabase PostgreSQL + pgvector（现有，复用）
- **向量化**：DashScope text-embedding-v3（1024 维，复用现有 embedding 设施）
- **LLM**：DashScope 千问（turbo/plus，用于知识提取和聚合分析）
- **缓存**：进程内 TTL 缓存（同记忆系统模式）
- **图查询**：PostgreSQL 递归 CTE（原生 SQL，无需 Neo4j）
- **复杂图算法**：NetworkX（按需引入，纯 Python 库）

---

## 4. 核心架构：分层提取

```
任务完成（Chat / Image / Video）
    ↓
┌─────────────────────────────────────────────────────────┐
│ 第一层：结构化指标（零 LLM 成本，每次必记）              │
│  → 写入 knowledge_metrics 表                             │
│  → 记录：模型、任务类型、成功/失败、耗时、错误码、参数    │
└─────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────┐
│ 第二层：高可信事件提取（LLM 提取，仅特定事件触发）       │
│  → 触发条件：                                            │
│    ├─ 智能重试成功（模型 A 失败 → 模型 B 成功）          │
│    ├─ 任务失败（明确的错误信息）                          │
│    └─ 用户重新生成（隐式负面反馈）[第二版加入]            │
│  → 千问提取知识 → 写入 knowledge_nodes + knowledge_edges │
└─────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────┐
│ 第三层：统计聚合知识（定时任务，从指标数据生成）[第二版]  │
│  → 每周从 knowledge_metrics 聚合                         │
│  → 千问分析统计数据 → 生成/更新 knowledge_nodes          │
│  → 基于样本量，不会过度泛化                               │
└─────────────────────────────────────────────────────────┘
```

**第一版交付范围**：第一层（指标）+ 第二层（重试/失败事件提取）+ 路由注入
**第二版迭代**：用户重新生成信号 + 第三层统计聚合

---

## 5. 目录结构

### 新增文件

| 文件 | 行数预估 | 职责 |
|------|---------|------|
| `backend/services/knowledge_service.py` | ~300 | 知识 CRUD + 向量搜索 + 去重更新 |
| `backend/services/knowledge_config.py` | ~150 | pgvector 直连 + embedding 客户端 + 缓存 + 格式化 |
| `backend/services/knowledge_extractor.py` | ~200 | 从高可信事件提取知识（LLM prompt + 解析 + 降级链） |
| `backend/services/graph_service.py` | ~120 | 图查询抽象层（递归 CTE 封装，未来可换图数据库） |
| `backend/migrations/023_add_knowledge_base.sql` | ~80 | 数据库迁移脚本（3 张表 + 索引） |
| `backend/data/seed_knowledge.json` | ~100 | 种子知识（模型能力、工具用法基础数据） |
| `backend/tests/test_knowledge_service.py` | ~250 | 单测 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `backend/core/config.py` | 新增 `kb_*` 配置项（~10 行） |
| `backend/services/handlers/chat_handler.py` | 完成后增加指标记录 + 重试时触发知识提取（~20 行） |
| `backend/services/handlers/message_mixin.py` | `_handle_complete_common` 末尾增加 Image/Video 指标记录（~15 行） |
| `backend/services/intent_router.py` | 路由前查询知识，注入 system prompt（~20 行） |

---

## 6. 数据库设计

### 表 1：knowledge_metrics（结构化指标，每次任务必记）

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | UUID | PK | gen_random_uuid() | 主键 |
| task_type | TEXT | NOT NULL | - | chat / image / video |
| model_id | TEXT | NOT NULL | - | 使用的模型 |
| status | TEXT | NOT NULL | - | success / failed |
| error_code | TEXT | - | NULL | 错误码 |
| cost_time_ms | INT | - | NULL | 耗时（毫秒） |
| prompt_tokens | INT | - | 0 | 输入 token 数（Chat） |
| completion_tokens | INT | - | 0 | 输出 token 数（Chat） |
| prompt_category | TEXT | - | NULL | 提示词分类 |
| params | JSONB | - | '{}' | 任务参数（分辨率、时长等） |
| retried | BOOLEAN | NOT NULL | FALSE | 是否经过智能重试 |
| retry_from_model | TEXT | - | NULL | 重试前的失败模型 |
| user_id | UUID | - | NULL | 用户 ID |
| created_at | TIMESTAMPTZ | NOT NULL | NOW() | 创建时间 |

**索引**：
- `idx_metrics_model_type`：(model_id, task_type) — 按模型+类型聚合
- `idx_metrics_created`：(created_at DESC) — 时间范围查询
- `idx_metrics_status`：(status) — 成功/失败过滤

---

### 表 2：knowledge_nodes（知识实体，图节点 + 向量）

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | UUID | PK | gen_random_uuid() | 主键 |
| category | TEXT | NOT NULL, CHECK | - | model / tool / experience |
| subcategory | TEXT | - | NULL | 细分：image_gen / chat / video_gen 等 |
| node_type | TEXT | NOT NULL | - | 节点类型：model / capability / parameter / pattern / error |
| title | TEXT | NOT NULL | - | 短标题（≤100 字） |
| content | TEXT | NOT NULL | - | 详细知识内容（≤1000 字） |
| metadata | JSONB | - | '{}' | 灵活元数据：model_id, tags, prompt_type 等 |
| embedding | vector(1024) | - | NULL | DashScope text-embedding-v3 向量 |
| source | TEXT | NOT NULL | 'auto' | 来源：auto / seed / manual / aggregated |
| confidence | FLOAT | NOT NULL | 0.5 | 置信度 0.0-1.0 |
| hit_count | INT | NOT NULL | 0 | 被检索命中次数 |
| scope | TEXT | NOT NULL | 'global' | 作用域：global / user:{id} |
| content_hash | TEXT | UNIQUE | - | 内容哈希（去重） |
| is_deleted | BOOLEAN | NOT NULL | FALSE | 软删除 |
| created_at | TIMESTAMPTZ | NOT NULL | NOW() | 创建时间 |
| updated_at | TIMESTAMPTZ | NOT NULL | NOW() | 更新时间 |

**索引**：
- `idx_nodes_category`：(category, subcategory) — 分类过滤
- `idx_nodes_scope`：(scope) — 作用域过滤
- `idx_nodes_embedding`：USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100) — 向量检索
- `idx_nodes_hash`：UNIQUE (content_hash) — 去重
- `idx_nodes_confidence`：(confidence DESC) — 按置信度排序/淘汰

**CHECK 约束**：
- `category IN ('model', 'tool', 'experience')`
- `source IN ('auto', 'seed', 'manual', 'aggregated')`

---

### 表 3：knowledge_edges（知识关系，图边）

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | UUID | PK | gen_random_uuid() | 主键 |
| source_id | UUID | FK, NOT NULL | - | 起始节点 |
| target_id | UUID | FK, NOT NULL | - | 目标节点 |
| relation_type | TEXT | NOT NULL | - | 关系类型 |
| weight | FLOAT | NOT NULL | 1.0 | 关系权重 |
| metadata | JSONB | - | '{}' | 关系元数据 |
| created_at | TIMESTAMPTZ | NOT NULL | NOW() | 创建时间 |

**索引**：
- `idx_edges_source`：(source_id) — 正向遍历
- `idx_edges_target`：(target_id) — 反向遍历
- `idx_edges_type`：(relation_type) — 按关系类型过滤

**外键**：
- source_id → knowledge_nodes(id) ON DELETE CASCADE
- target_id → knowledge_nodes(id) ON DELETE CASCADE

**关系类型枚举**（应用层控制）：
- `good_at`：模型擅长某能力
- `struggles_with`：模型在某场景表现差
- `better_than`：模型 A 在某场景优于模型 B
- `requires`：工具需要某参数
- `produces`：工具产出某类型结果
- `related_to`：通用关联

---

## 7. 核心流程设计

### 7.1 第一层：结构化指标记录

**触发时机**：每次任务完成/失败后（fire-and-forget）

```python
# chat_handler.py _stream_generate() 完成后
asyncio.create_task(
    knowledge_service.record_metric(
        task_type="chat",
        model_id=model_id,
        status="success",
        cost_time_ms=elapsed_ms,
        prompt_tokens=final_usage["prompt_tokens"],
        completion_tokens=final_usage["completion_tokens"],
        retried=bool(_retry_context),
        retry_from_model=_retry_context.failed_models[-1] if _retry_context else None,
        user_id=user_id,
    )
)

# message_mixin.py _handle_complete_common() 末尾
# Image/Video 同理，从 task 记录中提取信息
```

**零 LLM 成本，零失败风险。**

---

### 7.2 第二层：高可信事件知识提取

**触发条件**：

| 事件 | 触发位置 | 提取内容 |
|------|---------|---------|
| 智能重试成功 | `chat_handler._attempt_chat_retry` 返回 True | "模型A失败→模型B成功"的经验 |
| 任务失败（未重试） | `chat_handler.on_error` | 模型在某场景的失败模式 |
| Image/Video 失败 | `message_mixin._handle_error_common` | 模型参数组合的失败经验 |

**提取 Prompt（结构化输出）**：

```
你是 AI 系统的知识管理员。根据以下任务执行结果，提取可复用的系统知识。

任务信息：
- 类型：{task_type}
- 模型：{model_id}
- 状态：{status}
- 错误信息：{error_message}
- 重试信息：{retry_info}（从 {failed_model} 失败后切换到 {success_model}）

请以 JSON 数组格式返回知识条目（0-3 条，没有有价值的知识则返回空数组）：
[
  {
    "category": "model|tool|experience",
    "subcategory": "chat|image_generation|video_generation",
    "title": "简短标题（≤50字）",
    "content": "详细描述（≤200字）",
    "related_entities": ["model_id_1", "model_id_2"],
    "relations": [
      {"from": "entity_name", "to": "entity_name", "type": "better_than|struggles_with|good_at"}
    ],
    "confidence": 0.5-1.0
  }
]

提取规则：
1. 只提取关于模型能力、工具特性、参数效果的系统知识
2. 不提取用户个人信息或对话内容
3. 失败经验必须包含具体错误原因，不要笼统的"失败了"
4. 重试成功必须记录两个模型的对比结论
5. 置信度：重试对比=0.9，明确错误=0.8，推测性结论=0.5
```

**降级链**：qwen-turbo（3s）→ qwen-plus（3s）→ 跳过

---

### 7.3 知识写入流程（去重 + 图构建）

```
LLM 返回知识条目
    ↓
对每个条目：
  ├─ 计算 content_hash = sha256(category + title + content)
  ├─ 查询 knowledge_nodes WHERE content_hash = ?
  │  ├─ 已存在 → UPDATE confidence, updated_at, hit_count
  │  └─ 不存在 → 计算 embedding → INSERT knowledge_nodes
  │
  ├─ 查询向量相似度 > 0.9 的已有节点
  │  └─ 如有高度相似 → 合并（更新已有节点，不插入新节点）
  │
  └─ 处理 relations：
     ├─ 查找/创建 related_entities 对应的节点
     └─ INSERT knowledge_edges（source → relation_type → target）
```

---

### 7.4 知识检索（路由注入）

**触发时机**：`IntentRouter.route()` 调用千问前

```python
# intent_router.py
async def route(self, content, user_id, conversation_id):
    text = self._extract_text(content)

    # 查询相关知识（向量检索，不精排，< 50ms）
    knowledge_items = await knowledge_service.search_relevant(
        query=text,
        limit=5,
        threshold=0.5,
    )

    # 拼接到路由系统提示
    if knowledge_items:
        knowledge_text = "\n".join(f"- {k['title']}: {k['content']}" for k in knowledge_items)
        enhanced_prompt = ROUTER_SYSTEM_PROMPT + f"\n\n你已掌握的经验知识：\n{knowledge_text}"
    else:
        enhanced_prompt = ROUTER_SYSTEM_PROMPT

    # 调用千问路由（使用增强后的 system prompt）
    decision = await self._call_model(..., system_prompt=enhanced_prompt, ...)
```

**缓存策略**：知识检索结果缓存 10 分钟（TTL），减少重复查询。

---

### 7.5 GraphService 抽象层

```python
class GraphService:
    """图查询抽象层 — 当前用 PostgreSQL 递归 CTE，未来可换图数据库"""

    def __init__(self, db: Client):
        self.db = db

    async def find_related(
        self, node_id: str, depth: int = 2, relation_types: List[str] = None
    ) -> List[Dict]:
        """查找 N 跳以内的相关节点"""
        # 当前实现：PostgreSQL 递归 CTE

    async def find_path(
        self, from_id: str, to_id: str, max_depth: int = 3
    ) -> List[Dict]:
        """查找两个节点间的路径"""

    async def get_subgraph(
        self, node_ids: List[str], include_edges: bool = True
    ) -> Dict:
        """获取指定节点的子图"""

    async def add_edge(
        self, source_id: str, target_id: str, relation_type: str, weight: float = 1.0
    ) -> str:
        """添加关系边"""
```

**未来扩展**：如果 Step 2/3 阶段发现递归 CTE 不够用，只需新建 `Neo4jGraphService` 实现同一接口，调用方零改动。

---

## 8. 配置项（config.py 新增）

```python
# Knowledge Base
kb_enabled: bool = True                          # 知识库总开关
kb_extraction_model: str = "qwen-turbo"          # 知识提取模型
kb_extraction_fallback_model: str = "qwen-plus"  # 降级模型
kb_extraction_timeout: float = 3.0               # 提取超时（秒）
kb_search_limit: int = 5                         # 路由检索最大条数
kb_search_threshold: float = 0.5                 # 向量相似度阈值
kb_max_nodes: int = 5000                         # 知识节点上限
kb_cache_ttl: int = 600                          # 检索缓存 TTL（秒）
kb_confidence_boost: float = 0.1                 # 命中时置信度增量
kb_confidence_decay_days: int = 30               # 未命中衰减周期（天）
```

---

## 9. 种子知识设计

预置基础知识，避免冷启动时知识库为空。

```json
[
  {
    "category": "model",
    "subcategory": "chat",
    "node_type": "model",
    "title": "gemini-3-pro 模型特点",
    "content": "Google 最强推理模型，支持 PhD 级别推理、Google Search、函数调用。擅长长文写作、代码生成、复杂分析。上下文窗口 1M tokens。",
    "source": "seed",
    "confidence": 1.0,
    "metadata": {"model_id": "gemini-3-pro"}
  },
  {
    "category": "model",
    "subcategory": "chat",
    "node_type": "model",
    "title": "gemini-3-flash 模型特点",
    "content": "高性能快速推理模型，低延迟高吞吐。适合简单问答、日常对话、快速翻译。不支持 Google Search。",
    "source": "seed",
    "confidence": 1.0,
    "metadata": {"model_id": "gemini-3-flash"}
  },
  {
    "category": "model",
    "subcategory": "image_generation",
    "node_type": "model",
    "title": "nano-banana 图片生成能力",
    "content": "基础文生图模型，快速生成高质量图像。支持多种比例，不支持 4K 分辨率。适合快速出图、概念验证。",
    "source": "seed",
    "confidence": 1.0,
    "metadata": {"model_id": "google/nano-banana"}
  },
  {
    "category": "model",
    "subcategory": "image_generation",
    "node_type": "model",
    "title": "nano-banana-pro 高级图片生成",
    "content": "高级文生图模型，支持 1K/2K/4K 分辨率和参考图片。4K 分辨率耗时约为 1K 的 3 倍，但画质显著提升。适合商品主图、海报设计。",
    "source": "seed",
    "confidence": 1.0,
    "metadata": {"model_id": "nano-banana-pro"}
  },
  {
    "category": "model",
    "subcategory": "video_generation",
    "node_type": "model",
    "title": "sora-2 视频生成能力",
    "content": "支持文生视频和图生视频，10 秒和 15 秒两种时长。图生视频以第一帧为起点，效果更可控。支持去水印。",
    "source": "seed",
    "confidence": 1.0,
    "metadata": {"model_id": "sora-2-text-to-video"}
  },
  {
    "category": "tool",
    "subcategory": "image_generation",
    "node_type": "parameter",
    "title": "图片分辨率选择建议",
    "content": "1K 分辨率适合快速预览和社交媒体；2K 适合网页展示和电商详情页；4K 适合印刷品和大尺寸海报。分辨率越高，生成耗时和积分消耗越高。",
    "source": "seed",
    "confidence": 1.0,
    "metadata": {"related_models": ["nano-banana-pro"]}
  }
]
```

种子知识 `source=seed`，`confidence=1.0`，不会被自动提取的知识覆盖。

---

## 10. 开发任务拆分

### 阶段 1：数据库 + 基础设施

- [ ] 任务 1.1：创建迁移脚本 `023_add_knowledge_base.sql`（3 张表 + 索引 + 约束）
- [ ] 任务 1.2：`knowledge_config.py`（pgvector 直连 + embedding 客户端 + TTL 缓存）
- [ ] 任务 1.3：`config.py` 新增 `kb_*` 配置项
- [ ] 任务 1.4：`graph_service.py`（图查询抽象层，递归 CTE 封装）

### 阶段 2：知识服务核心

- [ ] 任务 2.1：`knowledge_service.py` — 指标记录（record_metric）
- [ ] 任务 2.2：`knowledge_service.py` — 知识 CRUD（add_knowledge / search_relevant / update / delete）
- [ ] 任务 2.3：`knowledge_service.py` — 去重逻辑（content_hash + 向量相似度合并）
- [ ] 任务 2.4：`knowledge_service.py` — 种子知识导入（load_seed_knowledge）

### 阶段 3：知识提取

- [ ] 任务 3.1：`knowledge_extractor.py`（提取 prompt + JSON 解析 + 降级链）
- [ ] 任务 3.2：`chat_handler.py` 增加指标记录钩子（fire-and-forget）
- [ ] 任务 3.3：`chat_handler.py` 重试成功时触发知识提取
- [ ] 任务 3.4：`message_mixin.py` 增加 Image/Video 指标记录 + 失败知识提取

### 阶段 4：知识注入路由

- [ ] 任务 4.1：`knowledge_service.py` 增加 `search_relevant()` 方法（向量检索 + 缓存）
- [ ] 任务 4.2：`intent_router.py` 路由前查询知识 + 注入 system prompt

### 阶段 5：种子数据 + 测试

- [ ] 任务 5.1：编写 `seed_knowledge.json`（模型能力 + 工具参数基础数据）
- [ ] 任务 5.2：`test_knowledge_service.py` 单测（指标记录 + CRUD + 搜索 + 去重 + 提取 + 图查询）

---

## 11. 依赖变更

**无需新增依赖**。全部复用现有：
- `pgvector`（已安装，Mem0 在用）
- `httpx`（已安装，DashScope API 调用）
- `loguru`（已安装，日志）
- Supabase Python SDK（已安装，数据库操作）

**可选依赖**（按需引入）：
- `networkx>=3.0`（复杂图算法时引入，当前阶段不需要）

---

## 12. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 提取 prompt 输出不稳定 | 高 | 结构化 JSON 输出 + 严格解析 + 解析失败则跳过 |
| 错误知识误导路由 | 高 | confidence 机制 + 种子知识不可覆盖 + 新提取知识初始置信度 0.5 |
| 知识提取增加千问成本 | 中 | 仅高可信事件触发（预计 <5% 的任务会触发提取） |
| 路由前检索增加延迟 | 中 | 仅向量检索（<50ms）+ 10 分钟 TTL 缓存 |
| pgvector 连接与 Mem0 冲突 | 低 | 独立连接实例，不共享 Mem0 的 pgvector 连接 |
| 知识节点爆炸 | 低 | 上限 5000 条 + confidence 淘汰机制 |
| 递归 CTE 性能（大图） | 低 | 当前数据规模远低于 PG 图查询上限；GraphService 抽象层支持未来切换 |

---

## 13. 升级路径（面向 ROADMAP 后续步骤）

| 阶段 | 知识库的角色 | 可能需要的升级 |
|------|------------|--------------|
| 第一步（当前） | 积累模型/工具执行经验 | 无 |
| 第二步（工具编排） | 存储工具注册表 + 工具间关系 | knowledge_edges 增加 `requires` / `produces` 关系类型 |
| 第三步（ERP 接入） | 存储商品/店铺/库存实体关系 | knowledge_nodes 增加 `business` 类别；scope 扩展为 org/team/user 层级 |
| 第四步（自主工具） | 存储自创工具代码 + 评估结果 | knowledge_nodes 增加 `code` 类别 + `executable` 字段 |

**关键设计**：nodes + edges + GraphService 抽象层确保每一步扩展只需**加字段/加类型**，不需要重构存储层和查询层。

---

## 14. 文档更新清单

- [ ] ROADMAP_智能Agent.md（任务三标记完成）
- [ ] FUNCTION_INDEX.md（新增函数）
- [ ] PROJECT_OVERVIEW.md（新增文件）

---

**确认后进入开发（`/everydayai-implementation`）**

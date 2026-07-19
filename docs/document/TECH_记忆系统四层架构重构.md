# 记忆系统四层架构重构 技术方案

> **历史方案，已废弃。** L2 Scene/L3 Persona 运行实现已在通用记忆 Phase 4.1
> 删除；当前设计与实施状态以 `TECH_Grok式通用记忆运行时重构.md` 为准。

> **版本**：v1.0
> **日期**：2026-05-16
> **等级**：A级（涉及 15+ 文件，核心链路重构）
> **参考**：[腾讯记忆架构拆解](./TECH_腾讯记忆架构拆解.md)
> **工期**：18-20 天（P0-P7，8 个 Phase）

---

## 一、目标与范围

### 要解决的问题
1. Mem0 黑盒提取质量差，无法控制提取逻辑
2. 纯向量检索 + 千问精排，成本高延迟大
3. 记忆碎片化，无结构、无去重、无可追溯性
4. 100条硬上限，无语义管理
5. 无上下文压缩，长对话 token 浪费严重

### 重构后的目标
- L1 结构化提取（3种类型 + 优先级 + 情境切分）
- RRF 混合检索（向量 + BM25），无需 LLM 精排
- L2 场景聚类（叙事文档，自动 MERGE/归档）
- L3 用户画像（四层深度扫描，增量更新）
- 三级上下文压缩（Mild/Aggressive/Emergency）
- 完整可追溯（L3→L2→L1→L0 原始对话）

### 不改的部分
- API 路由层（`api/routes/memory.py`）保持接口兼容
- 用户设置（`memory_settings.py`）保留
- 前端 memory 面板交互不变
- 多租户隔离机制（org_scoped）复用

---

## 二、数据库设计（P0）

### 新建 4 张表 + 修改 1 张表

#### 2.1 `memory_atoms`（L1 原子事实）
```sql
CREATE TABLE memory_atoms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id),
    user_id UUID NOT NULL REFERENCES users(id),
    
    -- 内容
    content TEXT NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('persona', 'episodic', 'instruction')),
    priority INTEGER NOT NULL DEFAULT 50,  -- 0-100, -1=死命令
    scene_name VARCHAR(100) DEFAULT '',
    
    -- 溯源
    source_message_ids UUID[] DEFAULT '{}',  -- 关联到 messages 表
    session_id UUID REFERENCES chat_sessions(id),
    
    -- 时间语义
    activity_start_time TIMESTAMPTZ,  -- 事件起始（episodic）
    activity_end_time TIMESTAMPTZ,    -- 事件结束（episodic）
    timestamps TIMESTAMPTZ[] DEFAULT '{}',  -- 合并历史时间线
    
    -- 向量
    embedding vector(1024),  -- text-embedding-v3 维度
    
    -- 全文搜索
    content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('chinese', content)
    ) STORED,
    
    -- 元数据
    metadata JSONB DEFAULT '{}',
    is_deleted BOOLEAN DEFAULT FALSE,  -- 软删除（冲突检测 update/merge 时）
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_atoms_org_user ON memory_atoms(org_id, user_id) WHERE NOT is_deleted;
CREATE INDEX idx_atoms_user_updated ON memory_atoms(user_id, updated_at DESC) WHERE NOT is_deleted;
CREATE INDEX idx_atoms_type ON memory_atoms(type) WHERE NOT is_deleted;
CREATE INDEX idx_atoms_scene ON memory_atoms(scene_name) WHERE NOT is_deleted;
CREATE INDEX idx_atoms_session ON memory_atoms(session_id);
CREATE INDEX idx_atoms_embedding ON memory_atoms USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_atoms_tsv ON memory_atoms USING gin (content_tsv);
```

#### 2.2 `memory_scenes`（L2 语义场景）
```sql
CREATE TABLE memory_scenes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id),
    user_id UUID NOT NULL REFERENCES users(id),
    
    -- 内容
    title VARCHAR(200) NOT NULL,       -- 场景标题（如"后端开发技术栈"）
    summary VARCHAR(500) NOT NULL,     -- 30-40字索引摘要
    content TEXT NOT NULL,             -- 完整场景文档（Markdown格式）
    
    -- 管理
    heat INTEGER DEFAULT 1,            -- 热度（越高越活跃）
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    
    -- 元数据
    metadata JSONB DEFAULT '{}',
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_scenes_org_user ON memory_scenes(org_id, user_id) WHERE status = 'active';
CREATE INDEX idx_scenes_heat ON memory_scenes(user_id, heat DESC) WHERE status = 'active';
```

#### 2.3 `memory_personas`（L3 用户画像）
```sql
CREATE TABLE memory_personas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id),
    user_id UUID NOT NULL REFERENCES users(id),
    
    -- 内容
    content TEXT NOT NULL,             -- 完整 Persona 文档（Markdown）
    archetype VARCHAR(200),            -- 一句话核心原型
    
    -- 版本管理
    version INTEGER DEFAULT 1,
    trigger_reason VARCHAR(500),       -- 触发原因
    
    -- 统计
    total_atoms_processed INTEGER DEFAULT 0,
    total_scenes INTEGER DEFAULT 0,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- 每用户每组织唯一（最新版本）
    UNIQUE(org_id, user_id)
);
```

#### 2.4 `memory_pipeline_state`（管道状态）
```sql
CREATE TABLE memory_pipeline_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id),
    user_id UUID NOT NULL REFERENCES users(id),
    session_id UUID REFERENCES chat_sessions(id),
    
    -- L1 状态
    conversation_count INTEGER DEFAULT 0,    -- 当前轮次计数
    warmup_threshold INTEGER DEFAULT 1,      -- Warm-up 当前阈值（0=已毕业）
    last_l1_at TIMESTAMPTZ,
    l1_cursor_timestamp TIMESTAMPTZ,         -- L0 增量游标
    last_scene_name VARCHAR(100),            -- L1 上次情境名（连续性）
    
    -- L2 状态
    last_l2_at TIMESTAMPTZ,
    l2_fire_time TIMESTAMPTZ,                -- 下次 L2 触发时间
    
    -- L3 状态
    atoms_since_last_persona INTEGER DEFAULT 0,
    last_persona_at TIMESTAMPTZ,
    request_persona_update BOOLEAN DEFAULT FALSE,
    persona_update_reason VARCHAR(500),
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(org_id, user_id, session_id)
);
```

#### 2.5 修改 `user_memory_settings`（增加字段）
```sql
ALTER TABLE user_memory_settings 
    ADD COLUMN IF NOT EXISTS max_scenes INTEGER DEFAULT 15,
    ADD COLUMN IF NOT EXISTS l1_trigger_every_n INTEGER DEFAULT 5,
    ADD COLUMN IF NOT EXISTS persona_trigger_every_n INTEGER DEFAULT 50;
```

---

## 三、Python 模块规划

### 目录结构
```
backend/services/memory/
├── __init__.py
├── memory_service.py          # 重写：统一 Facade（对外接口不变）
├── l1_extractor.py            # 新建：L1 原子事实提取
├── l1_dedup.py                # 新建：L1 冲突检测
├── l2_scene_manager.py        # 新建：L2 场景聚类
├── l3_persona_generator.py    # 新建：L3 用户画像
├── retrieval_pipeline.py      # 新建：RRF 混合检索
├── pipeline_scheduler.py      # 新建：L1→L2→L3 调度器
├── context_compressor.py      # 新建：三级上下文压缩
├── prompts/
│   ├── __init__.py
│   ├── l1_extraction.py       # 新建：L1 提取提示词
│   ├── l1_dedup.py            # 新建：L1 冲突检测提示词
│   ├── l2_scene.py            # 新建：L2 场景提示词
│   └── l3_persona.py          # 新建：L3 画像提示词
└── config.py                  # 新建：记忆系统配置集中管理
```

### 保留/改造的文件
```
backend/services/
├── memory_settings.py         # 保留（加字段）
├── memory_filter.py           # 删除（被 retrieval_pipeline.py RRF 替代）
├── memory_config.py           # 删除（Mem0 配置不再需要）
├── memory_service.py          # 重写为薄代理，转发到新模块
├── handlers/
│   ├── session_memory.py      # 保留（会话级增量提取，独立于持久记忆）
│   └── chat_context_mixin.py  # 改造（记忆注入逻辑替换）
```

---

## 四、核心模块设计

### 4.1 L1 提取器（`l1_extractor.py`）

```python
class L1Extractor:
    """L1 原子事实提取：从对话中提取结构化记忆"""
    
    async def extract(
        self,
        messages: list[dict],           # L0 对话消息
        session_id: UUID,
        user_id: UUID,
        org_id: UUID,
        previous_scene_name: str = "",  # 情境连续性
        max_new_messages: int = 10,
        max_background: int = 5,
    ) -> L1ExtractionResult:
        """
        流程：
        1. 质量过滤（长度、命令、注入检测）
        2. 分割背景+新消息
        3. LLM 提取（情境切分+记忆提取，单次调用）
        4. 冲突检测（RRF召回+LLM判断）
        5. 写入 PostgreSQL（向量+全文双索引）
        """
        
    async def _call_llm_extraction(
        self,
        new_messages: list[dict],
        background_messages: list[dict],
        previous_scene_name: str,
    ) -> list[SceneSegment]:
        """调千问 qwen-plus，输出 JSON 数组"""
        
    async def _embed_and_store(
        self,
        atoms: list[MemoryAtom],
        user_id: UUID,
        org_id: UUID,
        session_id: UUID,
    ) -> list[UUID]:
        """批量 embedding + 写入 memory_atoms"""


@dataclass
class L1ExtractionResult:
    success: bool
    extracted_count: int
    stored_count: int
    atom_ids: list[UUID]
    scene_names: list[str]
    last_scene_name: str | None
```

### 4.2 冲突检测（`l1_dedup.py`）

```python
class L1DedupService:
    """L1 冲突检测：新记忆 vs 已有记忆的去重判断"""
    
    async def batch_dedup(
        self,
        new_atoms: list[MemoryAtom],
        user_id: UUID,
        org_id: UUID,
    ) -> list[DedupDecision]:
        """
        两阶段：
        1. 候选召回（向量top5 或 BM25降级）
        2. LLM批量判断（store/update/merge/skip）
        """
    
    async def _recall_candidates_vector(
        self,
        atoms: list[MemoryAtom],
        user_id: UUID,
        org_id: UUID,
        top_k: int = 5,
    ) -> list[CandidateMatch]:
        """pgvector 余弦相似度召回"""
    
    async def _recall_candidates_bm25(
        self,
        atoms: list[MemoryAtom],
        user_id: UUID,
        org_id: UUID,
    ) -> list[CandidateMatch]:
        """tsvector 全文搜索降级召回"""

    async def apply_decisions(
        self,
        decisions: list[DedupDecision],
        new_atoms: list[MemoryAtom],
    ) -> list[UUID]:
        """执行 store/update/merge/skip，返回最终存储的 atom IDs"""


@dataclass
class DedupDecision:
    atom_id: UUID
    action: Literal["store", "update", "merge", "skip"]
    target_ids: list[UUID]           # update/merge 时要替换的旧记忆
    merged_content: str | None       # update/merge 后的新内容
    merged_type: str | None
    merged_priority: int | None
```

### 4.3 混合检索（`retrieval_pipeline.py`）

```python
class RetrievalPipeline:
    """RRF 混合检索：替代 Mem0 向量搜索 + 千问精排"""
    
    RRF_K = 60  # RRF 常数
    
    async def search(
        self,
        query: str,
        user_id: UUID,
        org_id: UUID,
        max_results: int = 5,
        strategy: Literal["hybrid", "embedding", "keyword"] = "hybrid",
        score_threshold: float = 0.3,
    ) -> list[ScoredMemory]:
        """
        hybrid 流程：
        1. 并行 pgvector cosine + tsvector BM25
        2. RRF 融合（K=60）
        3. 按分值降序取 top max_results
        """
    
    async def _search_vector(self, query_embedding, user_id, org_id, limit) -> list
    async def _search_bm25(self, query, user_id, org_id, limit) -> list
    
    def _rrf_merge(self, vector_results, bm25_results, max_results) -> list[ScoredMemory]:
        """RRF 融合算法"""

    def format_for_injection(self, memories: list[ScoredMemory]) -> str:
        """格式化为注入格式：- [type|scene] content (时间)"""


@dataclass
class ScoredMemory:
    atom_id: UUID
    content: str
    type: str
    priority: int
    scene_name: str
    score: float
    activity_time: str | None
```

### 4.4 L2 场景管理（`l2_scene_manager.py`）

```python
class L2SceneManager:
    """L2 场景聚类：将原子记忆整合为叙事文档"""
    
    MAX_SCENES = 15  # 默认上限
    
    async def extract_scenes(
        self,
        user_id: UUID,
        org_id: UUID,
        new_atoms: list[MemoryAtom],
    ) -> SceneExtractionResult:
        """
        流程：
        1. 加载现有场景索引
        2. 构建提示词（记忆+场景摘要+数量预警）
        3. 千问 Function Calling（create/update/merge/delete 4个工具）
        4. 执行 LLM 决策，更新 DB
        """
    
    # Function Calling 工具定义
    SCENE_TOOLS = [
        {"name": "create_scene", "params": {"title", "summary", "content"}},
        {"name": "update_scene", "params": {"scene_id", "content", "summary"}},
        {"name": "merge_scenes", "params": {"source_ids[]", "new_title", "new_summary", "new_content"}},
        {"name": "delete_scene", "params": {"scene_id"}},
    ]
```

### 4.5 L3 画像生成（`l3_persona_generator.py`）

```python
class L3PersonaGenerator:
    """L3 用户画像：综合场景生成/更新用户画像"""
    
    async def should_generate(self, user_id, org_id) -> tuple[bool, str]:
        """5级触发条件判断，返回 (should, reason)"""
    
    async def generate(
        self,
        user_id: UUID,
        org_id: UUID,
        trigger_reason: str,
    ) -> bool:
        """
        流程：
        1. 读取现有 persona（如有）
        2. 找出变化的场景
        3. 构建提示词（四层扫描模型）
        4. 千问生成/增量更新
        5. 写入 memory_personas（upsert）
        """
```

### 4.6 管道调度（`pipeline_scheduler.py`）

```python
class PipelineScheduler:
    """L1→L2→L3 管道调度器，per-session 状态管理"""
    
    def __init__(self):
        self._l1_tasks: dict[str, asyncio.Task] = {}
        self._l2_timers: dict[str, asyncio.Task] = {}
        self._l3_lock = asyncio.Lock()  # 全局互斥
    
    async def on_turn_committed(
        self,
        user_id: UUID,
        org_id: UUID,
        session_id: UUID,
        messages: list[dict],
    ):
        """对话结束回调：更新计数 → 判断 L1 触发"""
    
    async def _maybe_trigger_l1(self, state: PipelineState):
        """Warm-up 阈值判断 + 空闲超时"""
    
    async def _schedule_l2(self, state: PipelineState):
        """L1完成后调度 L2（downward-only timer）"""
    
    async def _maybe_trigger_l3(self, user_id, org_id):
        """L2完成后判断 L3 触发"""
```

### 4.7 上下文压缩（`context_compressor.py`）

```python
class ContextCompressor:
    """三级递进上下文压缩"""
    
    MILD_THRESHOLD = 0.50         # context >= 50%
    AGGRESSIVE_THRESHOLD = 0.85   # context >= 85%
    EMERGENCY_THRESHOLD = 0.95    # context >= 95%
    
    async def compress_if_needed(
        self,
        messages: list[dict],
        context_window: int = 200000,
    ) -> list[dict]:
        """
        根据 token 占比选择压缩策略：
        - Mild: 替换旧工具输出为摘要
        - Aggressive: 删除最旧40%消息
        - Emergency: 强制压至60%，保留≥4条
        """
    
    def _estimate_tokens(self, messages: list[dict]) -> int:
        """中文÷1.7 + 其他÷4（heuristic模式）"""
    
    async def _mild_compress(self, messages) -> list[dict]:
        """LLM 生成工具输出摘要替换原文"""
    
    def _aggressive_compress(self, messages) -> list[dict]:
        """删除最旧40% token 的消息"""
    
    def _emergency_compress(self, messages) -> list[dict]:
        """保留最新4条+system prompt"""
```

---

## 五、提示词设计

### 5.1 L1 提取提示词

直接移植腾讯方案（中文），核心要点：
- 角色：情境切分与记忆提取专家
- 三种类型严格定义（persona/episodic/instruction）
- 宁缺毋滥原则
- 输出 JSON 数组：`[{scene_name, message_ids, memories: [{content, type, priority, source_message_ids, metadata}]}]`

**LLM 选择**：`qwen-plus`（需要强推理能力）
**Token 预估**：system ~1500 + user ~2000 = 单次 ~3500 input + ~1000 output

### 5.2 L1 冲突检测提示词

直接移植腾讯方案，核心要点：
- 跨类型合并
- 多对多合并（target_ids 数组）
- 四种动作：store/update/merge/skip
- timestamps 并集
- 合并后 priority 酌情提升

**LLM 选择**：`qwen-turbo`（结构化判断，无需强推理）
**Token 预估**：单次 ~2000 input + ~500 output

### 5.3 L2 场景提示词

改造腾讯方案（文件操作→Function Calling），核心要点：
- 角色：记忆整合架构师，构建"数字第二大脑"
- 四种工具：create_scene/update_scene/merge_scenes/delete_scene
- 数量预警三级（红/橙/黄）
- 场景文档模板（核心特征/偏好/隐性信号/核心叙事/演变轨迹）
- UPDATE 优先原则

**LLM 选择**：`qwen-plus`（需要 Function Calling + 长文生成）
**Token 预估**：单次 ~5000 input + ~2000 output

### 5.4 L3 画像提示词

直接移植腾讯方案，核心要点：
- 四层深度扫描（基础锚点/兴趣图谱/交互协议/认知内核）
- Persona 模板（Archetype + 4 Chapter）
- 2000字符上限
- 增量模式：强化/补充/修正/重构/不改

**LLM 选择**：`qwen-plus`
**Token 预估**：单次 ~4000 input + ~2000 output

---

## 六、集成方案

### 6.1 ChatHandler 集成（`chat_context_mixin.py` 改造）

```python
# 改造前（当前）
async def _build_memory_prompt(self, user_id, query):
    memories = await memory_service.get_relevant_memories(user_id, query, ...)
    return build_memory_system_prompt(memories)

# 改造后
async def _build_memory_prompt(self, user_id, org_id, query):
    # 双部分注入
    pipeline = RetrievalPipeline()
    
    # 动态部分：L1 相关记忆（注入 user prompt 前面）
    scored = await pipeline.search(query, user_id, org_id)
    prepend_context = pipeline.format_for_injection(scored)
    
    # 稳定部分：L3 persona（注入 system prompt 末尾）
    persona = await get_persona(user_id, org_id)
    append_system = f"<user-persona>\n{persona.content}\n</user-persona>" if persona else ""
    
    return prepend_context, append_system
```

### 6.2 对话结束回调

```python
# 在 ChatHandler._on_turn_end() 中
async def _on_turn_end(self, ...):
    # 现有：fire-and-forget 提取
    # 改造：通知管道调度器
    await pipeline_scheduler.on_turn_committed(
        user_id=self.user_id,
        org_id=self.org_id,
        session_id=self.session_id,
        messages=[user_msg, assistant_msg],
    )
```

### 6.3 WebSocket 推送

```python
# L1 提取完成后推送
await ws_manager.send_to_user(user_id, {
    "type": "memory_extracted",
    "data": {
        "count": result.stored_count,
        "atoms": [{"content": a.content, "type": a.type} for a in stored_atoms[:3]],
    }
})
```

---

## 七、Mem0 迁移方案（P7）

### 7.1 数据迁移
```python
async def migrate_mem0_to_atoms():
    """将 Mem0 中的现有记忆迁移到 memory_atoms"""
    # 1. 调用 Mem0 API 获取用户所有记忆
    # 2. 批量 embedding（text-embedding-v3）
    # 3. 写入 memory_atoms（type='persona'，priority=50）
    # 4. 标记迁移完成
```

### 7.2 灰度切换
```python
# config.py 增加开关
memory_system_version: str = "v1"  # "v1"=Mem0旧版, "v2"=四层架构新版

# memory_service.py 路由
async def get_relevant_memories(self, ...):
    if config.memory_system_version == "v2":
        return await self._v2_retrieval(...)
    else:
        return await self._v1_mem0_retrieval(...)
```

### 7.3 下线 Mem0
- 迁移验证通过后，删除 `memory_config.py`（Mem0 配置）
- 删除 `memory_filter.py`（千问精排）
- 移除 `mem0ai` 依赖
- 清理 pgvector 中 Mem0 遗留数据

---

## 八、配置项

```python
# backend/services/memory/config.py

@dataclass
class MemoryConfig:
    # 开关
    enabled: bool = True
    system_version: str = "v2"  # v1=Mem0, v2=四层架构
    
    # L1 提取
    l1_extraction_model: str = "qwen-plus"
    l1_dedup_model: str = "qwen-turbo"
    l1_max_messages_per_extraction: int = 10
    l1_max_background_messages: int = 5
    l1_max_memories_per_session: int = 20
    
    # L2 场景
    l2_scene_model: str = "qwen-plus"
    l2_max_scenes: int = 15
    
    # L3 画像
    l3_persona_model: str = "qwen-plus"
    l3_trigger_every_n: int = 50
    l3_max_chars: int = 2000
    
    # 管道调度
    pipeline_every_n_conversations: int = 5
    pipeline_enable_warmup: bool = True
    pipeline_l1_idle_timeout: int = 60      # 秒
    pipeline_l2_delay_after_l1: int = 90    # 秒
    pipeline_l2_min_interval: int = 900     # 秒（15分钟）
    pipeline_l2_max_interval: int = 3600    # 秒（1小时）
    pipeline_session_active_hours: int = 24
    
    # 检索
    retrieval_strategy: str = "hybrid"  # hybrid/embedding/keyword
    retrieval_max_results: int = 5
    retrieval_score_threshold: float = 0.3
    retrieval_rrf_k: int = 60
    
    # 上下文压缩
    compress_mild_threshold: float = 0.50
    compress_aggressive_threshold: float = 0.85
    compress_emergency_threshold: float = 0.95
    compress_context_window: int = 200000
    
    # Embedding
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024
    embedding_timeout: int = 10  # 秒
```

---

## 九、Phase 详细实施计划

### P0（1天）：数据库 Schema + 迁移脚本
- [ ] 编写 `migrations/030_memory_v2_schema.sql`（4张新表）
- [ ] 编写 `migrations/031_memory_v2_indexes.sql`（向量+全文索引）
- [ ] 修改 `user_memory_settings` 增加字段
- [ ] 验证：迁移脚本在本地执行通过

### P1（4天）：L1 提取 + 去重
- [ ] 新建 `services/memory/` 目录结构
- [ ] 实现 `prompts/l1_extraction.py`（提示词）
- [ ] 实现 `prompts/l1_dedup.py`（冲突检测提示词）
- [ ] 实现 `l1_extractor.py`（提取管道）
- [ ] 实现 `l1_dedup.py`（冲突检测 + 向量/BM25 召回）
- [ ] 单元测试：提取准确率、去重正确性
- [ ] 验证：端到端对话提取 → 存储 → 查询

### P2（2天）：混合检索 + 召回注入
- [ ] 实现 `retrieval_pipeline.py`（RRF 混合检索）
- [ ] 改造 `chat_context_mixin.py`（双部分注入）
- [ ] 删除 `memory_filter.py`（千问精排不再需要）
- [ ] 单元测试：检索相关性、注入格式
- [ ] 验证：对话中记忆召回质量 vs 旧方案对比

### P3（2天）：管道调度
- [ ] 实现 `pipeline_scheduler.py`（Warm-up + Timer + 冷会话）
- [ ] 实现 `config.py`（配置集中管理）
- [ ] 集成到 ChatHandler 对话结束回调
- [ ] 单元测试：触发条件、并发安全
- [ ] 验证：新会话首条消息触发 L1，5轮后稳定触发

### P4（3天）：L2 场景聚类
- [ ] 实现 `prompts/l2_scene.py`（场景提示词 + Function Calling 工具定义）
- [ ] 实现 `l2_scene_manager.py`（场景 CRUD + 热度管理 + 数量控制）
- [ ] 集成到 pipeline_scheduler（L1→L2 链路）
- [ ] 单元测试：CREATE/UPDATE/MERGE/DELETE 各场景
- [ ] 验证：10+条记忆自动形成 2-3 个场景

### P5（2天）：L3 用户画像
- [ ] 实现 `prompts/l3_persona.py`（四层扫描提示词）
- [ ] 实现 `l3_persona_generator.py`（生成/触发/增量更新）
- [ ] 集成到 pipeline_scheduler（L2→L3 链路）
- [ ] 集成 persona 注入到 system prompt
- [ ] 验证：50条记忆后自动生成画像，增量更新正确

### P6（2天）：上下文压缩
- [ ] 实现 `context_compressor.py`（三级递进）
- [ ] 集成到 ChatHandler 的 `_build_llm_messages` 流程
- [ ] 单元测试：各阈值触发正确
- [ ] 验证：长对话（>50轮）token 占比下降

### P7（3天）：集成 + 迁移 + 测试
- [ ] 数据迁移脚本（Mem0 → memory_atoms）
- [ ] 灰度开关实现（v1/v2 切换）
- [ ] 重写 `memory_service.py` 为薄代理
- [ ] API 路由兼容性验证
- [ ] 前端面板适配（展示记忆类型、场景、画像）
- [ ] WebSocket 推送集成
- [ ] 全量集成测试
- [ ] 下线 Mem0（删除 memory_config.py、memory_filter.py、mem0ai 依赖）

---

## 十、风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| L1 提取提示词需反复调优 | P1 延期1-2天 | 准备 benchmark 数据集，量化评估准确率 |
| 千问 Function Calling 对 L2 场景工具支持不稳定 | P4 阻塞 | 备选：改用结构化 JSON 输出 + 代码解析执行 |
| 管道调度并发竞态 | 重复提取/丢失 | asyncio.Lock + DB 乐观锁（updated_at 校验） |
| 迁移期间新旧系统数据不一致 | 用户体验抖动 | 灰度期双写：新系统写入 + 旧系统保持只读 |
| Token 成本增加 | 月费用上升 | 监控单用户日消耗，设置每日上限（默认5万token/人/天） |

---

## 十一、成功标准

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| 记忆检索相关性（人工评估） | ~60% | ≥85% |
| 单次检索延迟 | 800ms（向量+精排） | ≤200ms（RRF无LLM） |
| 记忆去重率 | 0%（无去重） | ≥70%（语义重复被合并） |
| 长对话 token 消耗 | 无压缩 | 降低40-60% |
| 用户画像覆盖率 | 0% | ≥80%（活跃用户7天内生成） |

---

## 附录：文档更新清单

重构完成后需同步更新：
- `docs/PROJECT_OVERVIEW.md`：新增 memory/ 目录说明
- `docs/FUNCTION_INDEX.md`：新增所有 public 函数
- `docs/CURRENT_ISSUES.md`：关闭记忆相关 issue
- `.env.example`：新增配置项（如有）

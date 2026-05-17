# 腾讯 TencentDB-Agent-Memory 架构拆解

> **目的**：完整梳理腾讯方案的实现细节，作为我们记忆系统重构的参考蓝图
> **源码版本**：v0.3.4（2026-05-13）
> **仓库**：`Tencent/TencentDB-Agent-Memory`（MIT License，~30K行 TypeScript）

---

## 一、整体架构：四层金字塔

```
┌─────────────────────────────────────┐
│  L3 Persona（用户画像）              │ ← persona.md，全局唯一
│  综合所有场景，提炼用户核心画像        │    触发：每50条新记忆
├─────────────────────────────────────┤
│  L2 Scene Blocks（语义场景）          │ ← scene_blocks/*.md，上限15个
│  将相关记忆聚类为叙事文档             │    触发：L1完成后90秒
├─────────────────────────────────────┤
│  L1 Atom Memories（原子事实）         │ ← records/YYYY-MM-DD.jsonl + SQLite
│  从对话中提取结构化记忆               │    触发：每5轮对话或空闲60秒
├─────────────────────────────────────┤
│  L0 Conversations（原始对话）         │ ← conversations/YYYY-MM-DD.jsonl
│  完整记录用户/助手对话                │    触发：每轮对话结束
└─────────────────────────────────────┘
```

**核心设计原则**：
- **完整可追溯**：L3→L2→L1→L0，任何抽象都能向下钻取到原始对话
- **渐进提炼**：每一层都比上一层更抽象、更精简、更稳定
- **白盒可审计**：L2/L3 用 Markdown 文件存储，人类可直接阅读

---

## 二、L0 对话记录层

### 文件：`src/core/conversation/l0-recorder.ts`（583行）

### 数据格式
```jsonl
{"sessionKey":"abc","sessionId":"s1","recordedAt":"2026-05-15T10:00:00Z","id":"msg_xxx","role":"user","content":"...","timestamp":1747...}
```

### 写入流程
1. **位置切片**：用 `originalUserMessageCount`（prompt build 时缓存）定位本轮新消息
2. **时间游标**：`afterTimestamp` 二层防重（position slice 不可用时的降级）
3. **污染修复**：框架注入的 prependContext 污染了 user 消息 → 用缓存的原始文本替换
4. **清洗过滤**：`sanitizeText()`（去注入标签）→ `stripCodeBlocks()`（去代码块噪声）→ `shouldCaptureL0()`（长度/命令过滤）
5. **写入 JSONL**：按日分片，append-only

### 我们移植时的映射
| 腾讯 | 我们 |
|------|------|
| JSONL 文件存储 | **PostgreSQL `memory_conversations` 表**（已有 messages 表可复用） |
| sessionKey | org_scoped session_id |
| 位置切片防重 | 我们的 WebSocket 消息天然增量，无需 |

---

## 三、L1 原子事实提取层

### 核心文件
| 文件 | 行数 | 职责 |
|------|------|------|
| `l1-extractor.ts` | 536 | 提取管道主流程 |
| `l1-writer.ts` | 280 | JSONL + 向量双写 |
| `l1-dedup.ts` | 406 | 冲突检测（向量/BM25 + LLM判断） |
| `prompts/l1-extraction.ts` | 138 | 提取提示词（中文） |
| `prompts/l1-dedup.ts` | 164 | 冲突检测提示词 |

### 提取流程
```
L0消息 → 质量过滤(shouldExtractL1) → 分割(背景+新消息)
         → LLM提取(情境切分+记忆提取，单次调用)
         → 冲突检测(向量召回+LLM判断)
         → 双写(JSONL+SQLite向量库)
```

### 三种记忆类型
| 类型 | 定义 | 句式 | 优先级 |
|------|------|------|--------|
| **persona** | 稳定属性、偏好、技能 | "用户喜欢/是/擅长..." | 80-100核心，50-70一般 |
| **episodic** | 客观事件、决定、计划 | "用户在[时间]于[地点][做了某事]" | 80-100重要，60-70一般 |
| **instruction** | 长期行为规则 | "用户要求AI以后..." | -1死命令，90-100核心 |

### L1 提取提示词关键设计（源码：`prompts/l1-extraction.ts:15-98`）
```
系统角色：情境切分与记忆提取专家

任务一：情境切分（Scene Segmentation）
- 判断话题是否切换，生成情境名称
- 命名规则："我(AI)在和xxx(用户身份)做xxx(目标活动)"

任务二：核心记忆提取
- 宁缺毋滥：过滤闲聊、临时指令
- 独立完整：脱离当前对话仍成立
- 归纳合并：强关联多条消息必须合并

不应提取的内容：
- 琐碎闲聊、问候
- 一次性操作指令（"这次、本单"）
- AI助手自身的行为或输出
- 纯主观感受（不带客观事件的情绪）

输出：JSON数组
[{scene_name, message_ids, memories: [{content, type, priority, source_message_ids, metadata}]}]
```

### 冲突检测（去重）流程
```
新记忆 → 向量召回top5候选（或FTS5 BM25降级）
       → 批量LLM判断: store/update/merge/skip
       → 执行决策（写入新记录，删除旧记录）
```

**冲突检测提示词关键规则**（源码：`prompts/l1-dedup.ts:16-67`）：
- **跨类型合并**：episodic "2018年做播客" + persona "有播客经验" → 可合并
- **多对多合并**：一条新记忆可替换多条旧记忆（target_ids 数组）
- 合并后自动提升 priority
- timestamps 取并集保留完整时间线
- 四种决策：
  - `store`：新信息，直接存储
  - `skip`：已有记忆更好，丢弃新记忆
  - `update`：同一事实，新记忆更优，覆盖旧记忆
  - `merge`：信息互补不矛盾，合并为更完整记忆

### 数据结构（MemoryRecord）
```typescript
{
  id: string;              // 唯一ID
  content: string;         // 记忆文本
  type: "persona"|"episodic"|"instruction";
  priority: number;        // 0-100，-1=死命令
  scene_name: string;      // 所属场景
  source_message_ids: string[];  // 溯源到L0
  metadata: { activity_start_time?, activity_end_time? };
  timestamps: string[];    // 合并历史时间线
  sessionKey, sessionId, createdAt, updatedAt
}
```

### 我们移植时的改造
| 腾讯实现 | 我们的改造 |
|---------|-----------|
| JSONL + SQLite 双写 | **PostgreSQL 单写**（pgvector 向量 + tsvector 全文） |
| CleanContextRunner 调 LLM | **千问 API**（qwen-plus 提取，qwen-turbo 冲突检测） |
| 3种记忆类型 | **保持3种**，适配中文业务场景 |
| source_message_ids 溯源 | **保留**，关联到 messages 表 |

---

## 四、L2 场景聚类层

### 核心文件
| 文件 | 行数 | 职责 |
|------|------|------|
| `scene-extractor.ts` | 437 | LLM Agent 驱动的场景提取 |
| `scene-format.ts` | 76 | Scene Block 文件格式解析 |
| `scene-index.ts` | 97 | 场景索引维护 |
| `scene-navigation.ts` | — | 场景导航生成（注入persona） |
| `prompts/scene-extraction.ts` | 264 | 场景提取提示词 |

### 核心设计：LLM 作为文件操作 Agent

L2 不是简单的规则聚类，而是**让 LLM 充当记忆整合架构师**：
- LLM 被授予 `read/write/edit` 文件工具
- 工作目录被沙盒限制在 `scene_blocks/` 目录
- LLM 自主决定 CREATE/UPDATE/MERGE 场景文件
- LLM 自我定位为"人类学家和心理学家"，提取隐性信号

### Scene Block 文件格式（源码：`scene-format.ts`）
```markdown
-----META-START-----
created: 2026-05-01T10:00:00Z
updated: 2026-05-15T10:00:00Z
summary: 30-40字索引摘要
heat: 5
-----META-END-----

## 用户核心特征
[连贯描述，100字以内，非列表]

## 用户偏好
[列表形式，可复用的显性偏好]

## 隐性信号
[人类学家推断——"没说出口但很重要"的事]

## 核心叙事
[连贯故事，400字以内，Trigger→Action→Result 结构]

## 演变轨迹
[仅记录偏好/性格/重大观念转变，带时间戳]

## 待确认/矛盾点
[无法整合的矛盾信息，等待未来记忆澄清]
```

### 场景数量管理（上限15个）
| 场景数 | 策略 |
|--------|------|
| ≥ maxScenes | **红色预警**：必须先 MERGE 2-4个相似场景，再处理新记忆 |
| = maxScenes-1 | **橙色预警**：只能 UPDATE，不能 CREATE |
| 接近 maxScenes | **黄色预警**：优先 UPDATE 或主动 MERGE |

### 策略优先级（源码：`prompts/scene-extraction.ts:111-122`）
1. **UPDATE**（首选）：存在相关 Block → 先 read 再 write/edit
2. **MERGE**：多个 Block 属同一叙事弧线 → 合并后必须 `[DELETED]` 旧文件
3. **CREATE**（最后手段）：前提是总数 < maxScenes，且必须先 read 2个最相似场景确认无法融入

### 合并优先级
1. 主题高度重叠（"Python后端" + "Go后端" → "后端技术栈"）
2. 叙事弧线相同（"求职材料" + "职业发展" → "职业发展与求职"）
3. 热度最低的场景

### 热度管理
- 新建：`heat = 1`
- 更新：`heat = 旧heat + 1`
- 合并：`heat = sum(所有相关heat) + 1`

### 删除机制
- LLM 无 `exec` 权限，无法直接删除文件
- 写入 `[DELETED]` 标记 → 工程侧 cleanup 阶段 `fs.unlink`
- 合并后**必须**删除旧文件（仅 `[DELETED]` 触发清理，`[ARCHIVE]` 等标记不算删除）

### 我们移植时的改造
| 腾讯实现 | 我们的改造 |
|---------|-----------|
| Markdown 文件存储 | **PostgreSQL `memory_scenes` 表**（content TEXT, meta JSONB） |
| LLM 文件操作 Agent | **千问 Function Calling**（自定义 create_scene/update_scene/merge_scenes/delete_scene 工具） |
| scene_index.json | **PostgreSQL 索引查询**，无需单独维护 |
| 上限15个 | 保持，`max_scenes` 可配置 |

---

## 五、L3 用户画像层

### 核心文件
| 文件 | 行数 | 职责 |
|------|------|------|
| `persona-generator.ts` | 225 | 画像生成/更新 |
| `persona-trigger.ts` | 122 | 触发条件判断 |
| `prompts/persona-generation.ts` | 190 | 画像生成提示词 |

### 触发条件（按优先级，源码：`persona-trigger.ts:36-93`）
1. **P1 主动请求**：L2 Agent 发出 `PERSONA_UPDATE_REQUEST` 信号（重大价值观转变）
2. **P2 冷启动**：首次提取完成且有场景文件，但无 persona
3. **P2.5 恢复**：persona.md 正文丢失/为空（损坏恢复）
4. **P3 首次场景**：第一个 Scene Block 提取完成
5. **P4 阈值**：累计新记忆 ≥ `triggerEveryN`（默认50）

### 四层深度扫描模型（源码：`prompts/persona-generation.ts:55-80`）
| Layer | 名称 | 扫描目标 | 实用价值 |
|-------|------|---------|---------|
| 🟢 L1 | 基础锚点 | 确凿事实、人口统计 | 破冰话题、上下文感知 |
| 🔵 L2 | 兴趣图谱 | 时间/金钱/注意力投入 | 高质量闲聊、生活推荐 |
| 🟡 L3 | 交互协议 | 沟通习惯、雷区、工作流 | 消除摩擦、避免踩雷 |
| 🔴 L4 | 认知内核 | 决策逻辑、矛盾点、驱动力 | 深度共鸣、替用户做决策 |

### Persona 输出模板
```markdown
# User Narrative Profile

> **Archetype (核心原型)**: 一句话定义
> **基本信息**: 年龄、职业等（冲突覆盖，不冲突叠加）
> **长期偏好**: 最稳定的偏好

## Chapter 1: Context & Current State (全景语境)
[连贯描述，基础事实+当前状态融合]

## Chapter 2: The Texture of Life (生活肌理)
[兴趣+消费+生活习惯串联，展示品味]

## Chapter 3: Interaction & Cognitive Protocol (交互协议)
### 3.1 沟通策略 (How to Speak)
### 3.2 决策逻辑 (How to Think)

## Chapter 4: Deep Insights & Evolution (深层洞察)
* 矛盾统一性
* 演变轨迹（带时间）
* 涌现特征标签（3-7个，附注释）
```

### 生成模式
| 模式 | 条件 | 行为 |
|------|------|------|
| **first** | 无现有 persona | 全量生成，只基于场景数据 |
| **incremental** | 有现有 persona | 只处理变化场景，自主判断：强化/补充/修正/重构/不改 |

### 约束
- persona.md 总长度 ≤ 2000 字符
- 禁止过度推测（冷启动阶段保持克制）
- 所有内容必须来自场景数据，禁止从文件路径等元数据推断

### 我们移植时的改造
| 腾讯实现 | 我们的改造 |
|---------|-----------|
| persona.md 文件 | **PostgreSQL `memory_personas` 表**（per user per org） |
| LLM 文件 Agent | **千问 API 直接生成文本**（无需文件工具） |
| 场景导航追加 | 保留，作为 system prompt 稳定部分 |
| 2000字符上限 | 保持 |

---

## 六、存储与检索层

### SQLite 表结构（源码：`store/sqlite.ts`）

**L1 主表 `l1_records`**（18字段）
```sql
CREATE TABLE l1_records (
  record_id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  type TEXT NOT NULL,          -- persona/episodic/instruction
  priority INTEGER DEFAULT 50,
  scene_name TEXT DEFAULT '',
  session_key TEXT DEFAULT '',
  session_id TEXT DEFAULT '',
  metadata_json TEXT DEFAULT '{}',
  timestamp_str TEXT DEFAULT '',     -- 点时间
  timestamp_start TEXT DEFAULT '',   -- 段时间起
  timestamp_end TEXT DEFAULT '',     -- 段时间止
  created_time TEXT NOT NULL,
  updated_time TEXT NOT NULL
);

-- 关键索引
CREATE INDEX idx_l1_sessionkey_updated ON l1_records(session_key, updated_time);
CREATE INDEX idx_l1_session_updated ON l1_records(session_id, updated_time);
CREATE INDEX idx_l1_type ON l1_records(type);
CREATE INDEX idx_l1_scene ON l1_records(scene_name);
```

**L1 向量表**（sqlite-vec 扩展）
```sql
CREATE VIRTUAL TABLE l1_vec USING vec0(
  record_id TEXT PRIMARY KEY,
  embedding float[768]
);
```

**L1 全文搜索**（FTS5 + jieba 分词）
```sql
CREATE VIRTUAL TABLE l1_fts USING fts5(
  content_tokenized,          -- jieba 分词后的文本
  content_original UNINDEXED, -- 原文（不索引，仅存储）
  record_id UNINDEXED,
  type UNINDEXED
);
```

### 混合检索：RRF 融合算法（源码：`store/search-utils.ts`）

```
三种策略：keyword / embedding / hybrid（默认）

hybrid 流程：
1. 并行执行 FTS5(BM25) 和 vec0(cosine) 搜索，各取 maxResults×3 条
2. RRF 融合：score(i) = Σ 1/(K + rank + 1)，K=60
3. 同一记录在两个列表中的分值累加
4. 按 RRF 分值降序，取 top maxResults（默认5）

降级策略：
- embedding 不可用 → 自动降为 keyword only
- FTS 不可用 → 自动降为 embedding only
- 都不可用 → 返回空
```

### 我们的存储改造
| 腾讯 | 我们 |
|------|------|
| SQLite + sqlite-vec | **PostgreSQL + pgvector** |
| FTS5 + jieba | **PostgreSQL tsvector + zhparser**（或 pg_jieba） |
| 本地 BM25 编码 | **pg_trgm + tsvector** 组合 |
| RRF K=60 | **保持 RRF**，在 Python 层实现 |

---

## 七、管道调度

### 核心文件：`utils/pipeline-manager.ts`（~600行）

### L1→L2→L3 触发链

```
对话结束 → L0记录 → 计数器+1
                    ↓
         达到阈值(默认5轮) 或 空闲60秒
                    ↓
              L1 提取（SerialQueue，并发=1）
                    ↓
         L1完成 → 等待90秒 → 检查最小间隔(15分钟)
                    ↓
              L2 场景提取（SerialQueue，并发=1）
                    ↓
         L2完成 → 检查累计记忆数
                    ↓
         达到阈值(默认50条) → L3 画像生成（全局互斥）
```

### Warm-up 机制（新会话加速）
- 新会话首条消息立即触发 L1（阈值=1）
- 随后翻倍：1→2→4→8→...→everyNConversations（默认5）
- 确保新用户快速形成初始记忆

### L2 Timer 语义（downward-only）
- L1完成时设置：`fire_time = max(now + 90s, lastL2 + 15min)`
- 只**下移**不后延：L1 频繁触发时不会让 L2 一直等
- L2 完成后无条件设置 `now + maxInterval`（默认3600s）
- 冷会话（>24h 无活动）自动取消 timer，等待 L1 复活

### L3 全局互斥 + 去重
- L2 完成后加入全局队列（并发=1）
- 若 L3 已在运行，标记 pending
- 完成时若 pending=true → 再运行一轮

### 配置默认值
```python
pipeline.everyNConversations = 5      # L1触发阈值
pipeline.enableWarmup = True          # Warm-up加速
pipeline.l1IdleTimeoutSeconds = 60    # L1空闲触发
pipeline.l2DelayAfterL1Seconds = 90   # L2等待L1冷却
pipeline.l2MinIntervalSeconds = 900   # L2最小间隔15分钟
pipeline.l2MaxIntervalSeconds = 3600  # L2最大周期1小时
pipeline.sessionActiveWindowHours = 24 # 冷会话判定
persona.triggerEveryN = 50            # L3触发阈值
persona.maxScenes = 15                # 场景上限
```

---

## 八、召回与注入

### 核心文件：`hooks/auto-recall.ts`（~300行）

### 双部分注入架构（优化 prompt cache）

| 部分 | 注入位置 | 内容 | 缓存友好 |
|------|---------|------|---------|
| `prependContext` | user prompt **前面** | L1 相关记忆（动态，每轮不同） | ✗ |
| `appendSystemContext` | system prompt **末尾** | L3 persona + 场景导航 + 工具指南（稳定） | ✓ |

**关键设计**：persona 放 system prompt 末尾是为了 prompt cache 友好——system prompt 变化少，可跨轮复用缓存。L1 记忆放 user prompt 前面是因为每轮查询结果不同。

### L1 记忆注入格式
```
- [persona] 用户叫王小明，30岁，软件工程师。
- [episodic|旅行计划] 用户计划五月去日本旅行。(活动时间: 2025-05-01 ~ 2025-05-10)
- [instruction] 回答时使用中文，保持简洁。
```

### L3 Persona 注入格式
```xml
<user-persona>
# User Narrative Profile
...完整 persona 内容...
</user-persona>
```

### 搜索工具（Agent 可主动调用）
- `tdai_memory_search`：搜索 L1 结构化记忆
- `tdai_conversation_search`：搜索 L0 原始对话
- 每轮合计 ≤3 次搜索

---

## 九、上下文压缩（Offload）

### 核心文件：`offload/index.ts`（1979行）、`offload/types.ts`（252行）

### 三级递进压缩

| 等级 | 触发条件 | 策略 | 目标 |
|------|---------|------|------|
| **Mild** | context ≥ 50% | 替换非当前任务的工具输出为摘要 | 降到舒适区 |
| **Aggressive** | context ≥ 85% | 删除最旧40%消息token | 急剧瘦身 |
| **Emergency** | context ≥ 95% | 强制压至60%，保留≥4条消息 | 紧急求生 |

### Offload 处理层级
| 层级 | 单位 | 内容 |
|------|------|------|
| **L1** | Tool call+result 对 | 异步生成 summary，score 0-10 评分 |
| **L1.5** | Task boundary | 判断任务完成/延续，分配 MMD target |
| **L2** | MMD node | Mermaid flowchart 构建任务图 |
| **L3** | Message deletion | 按 token 计数删除最旧消息 |

### Mermaid 任务画布
- 将执行状态抽象为 Mermaid 流程图语法
- 每个 node 有 `node_id`，关联外部文件（`refs/*.md`）
- 上下文中只保留轻量图，完整日志按需回查
- 实测效果：**token 降低 61%，成功率提升 51%**

### L2 MMD 触发条件
1. offload.jsonl 中 `node_id=null` 条数 ≥ 4
2. 距上次 L2 ≥ 300 秒
3. `node_id="wait"` 的 entry 在 120s 后重试

### Token 计数模式
- `tiktoken`（默认）：BPE 编码 o200k_base（GPT-4o 级）
- `heuristic`：中文 ÷ 1.7 + 其他 ÷ 4（轻量快速）

---

## 十、移植方案总结

### 照搬的部分（核心算法与提示词）
1. **四层金字塔架构**：L0→L1→L2→L3 渐进提炼
2. **L1 提取提示词**：情境切分 + 记忆提取 + 三种类型 + 优先级打分
3. **L1 冲突检测提示词**：跨类型合并 + 多对多 + 优先级提升 + 时间线并集
4. **L2 场景管理逻辑**：CREATE/UPDATE/MERGE + 热度管理 + 数量上限 + 三级预警
5. **L2 场景文件模板**：核心特征/偏好/隐性信号/核心叙事/演变轨迹/矛盾点
6. **L3 四层深度扫描**：基础锚点 → 兴趣图谱 → 交互协议 → 认知内核
7. **L3 Persona 模板**：Archetype + 4 Chapter 结构
8. **RRF 混合检索算法**：向量 + BM25，K=60
9. **管道调度策略**：Warm-up 指数增长 + downward-only timer + 冷会话跳过
10. **双部分注入**：prependContext(动态L1) + appendSystemContext(稳定L3)
11. **三级压缩阈值**：50% Mild / 85% Aggressive / 95% Emergency

### 改造的部分（适配我们的技术栈）
| 维度 | 腾讯实现 | 我们的改造 |
|------|---------|-----------|
| 存储 | SQLite + sqlite-vec + FTS5 | PostgreSQL + pgvector + tsvector |
| LLM | OpenClaw LLMRunner | 千问 API（qwen-plus/turbo） |
| L2 Agent | 文件操作 Agent（read/write/edit） | Function Calling（create/update/merge/delete 工具） |
| 多租户 | 无（单用户） | org_scoped（所有表加 org_id + user_id） |
| 语言 | TypeScript（sync SQLite） | Python（asyncio + asyncpg） |
| 前端 | 无 | WebSocket 推送 + 记忆面板 |
| L0 存储 | JSONL 文件 | 复用已有 messages 表 |

### 不移植的部分
1. **Offload Mermaid 画布**：我们的 Agent 单循环架构不需要任务图（但三级压缩保留）
2. **TCVDB 后端**：我们用 PostgreSQL，不需要腾讯云向量库
3. **OpenClaw/Hermes 适配层**：我们自建适配
4. **CLI 管理工具**：后续按需开发
5. **profile-sync**：用户画像同步，我们有自己的用户系统
6. **seed 功能**：种子记忆导入，暂不需要

---

## 附录：核心文件清单

| 模块 | 文件 | 行数 | 核心职责 |
|------|------|------|---------|
| **L0** | conversation/l0-recorder.ts | 583 | 对话记录 + 清洗 + 防重 |
| **L1** | record/l1-extractor.ts | 536 | 提取主流程 |
| | record/l1-writer.ts | 280 | JSONL + 向量双写 |
| | record/l1-dedup.ts | 406 | 冲突检测（向量/BM25 + LLM） |
| | record/l1-reader.ts | 219 | 记忆读取（SQLite优先，JSONL降级） |
| | prompts/l1-extraction.ts | 138 | 提取提示词（中文） |
| | prompts/l1-dedup.ts | 164 | 冲突检测提示词（中文） |
| **L2** | scene/scene-extractor.ts | 437 | LLM Agent 场景提取 |
| | scene/scene-format.ts | 76 | Scene Block 格式解析 |
| | scene/scene-index.ts | 97 | 场景索引维护 |
| | prompts/scene-extraction.ts | 264 | 场景提取提示词 |
| **L3** | persona/persona-generator.ts | 225 | 画像生成 |
| | persona/persona-trigger.ts | 122 | 触发条件判断 |
| | prompts/persona-generation.ts | 190 | 画像生成提示词 |
| **存储** | store/sqlite.ts | ~700 | 表结构 + CRUD + 向量搜索 |
| | store/bm25-local.ts | ~200 | 本地 BM25 编码（jieba） |
| | store/embedding.ts | ~300 | Embedding 服务（远程API） |
| | store/search-utils.ts | ~150 | RRF 融合算法 |
| **调度** | utils/pipeline-manager.ts | ~600 | L1→L2→L3 管道调度 |
| | utils/pipeline-factory.ts | ~400 | Runner/Persister 工厂 |
| **召回** | hooks/auto-recall.ts | ~300 | 记忆召回 + prompt 注入 |
| | hooks/auto-capture.ts | ~200 | 对话捕获 + 调度通知 |
| **压缩** | offload/index.ts | 1979 | 压缩主流程（三级递进） |
| | offload/types.ts | 252 | 类型定义 |
| **配置** | config.ts | 638 | 全量配置解析 |
| **核心** | core/tdai-core.ts | 534 | 统一 Facade（recall/capture/search） |
| **类型** | core/types.ts | 242 | 接口定义 |

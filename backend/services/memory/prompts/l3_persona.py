"""
L3 画像生成提示词

移植自腾讯 TencentDB-Agent-Memory prompts/persona-generation.ts
四层深度扫描模型 + Persona 输出模板
"""

from __future__ import annotations

# ============================================================
# System Prompt
# ============================================================

PERSONA_GENERATION_SYSTEM_PROMPT = """# 🧬 Persona Architect - Incremental Evolution Protocol

你的任务是基于场景数据（L2 Scene Blocks）生成或更新用户画像（Persona）。

## 核心运作逻辑

执行以下**四层深度扫描**：

### 🟢 Layer 1: 基础锚点 (The Base & Facts)
- **扫描目标**: 确凿的事实、人口统计学特征、当前状态
- **实用价值**: 为 Agent 提供破冰话题和上下文感知

### 🔵 Layer 2: 兴趣图谱 (The Interest Graph)
- **扫描目标**: 用户投入时间、金钱或注意力的事物
- **提取原则**: 区分活跃度（活跃爱好 / 被动消费 / 休眠兴趣）
- **实用价值**: 让 Agent 能够进行高质量的闲聊和生活推荐

### 🟡 Layer 3: 交互协议 (The Interface)
- **扫描目标**: 用户的沟通习惯、雷区、工作流偏好
- **实用价值**: 指导 Agent 如何说话、如何交付结果，避免踩雷

### 🔴 Layer 4: 认知内核 (The Core)
- **扫描目标**: 决策逻辑、矛盾点、终极驱动力
- **实用价值**: 让 Agent 成为能够替用户做决策的"副驾驶"

## 输出模板

请参考以下 Markdown 格式输出（信息不足时可精简章节）：

```
# User Narrative Profile

> **Archetype (核心原型)**: [一句话定义]

> **基本信息**
- [用户基本信息，冲突覆盖，不冲突叠加]

> **长期偏好**
- [最稳定且可复用的偏好]

## Chapter 1: Context & Current State (全景语境)
[连贯描述，基础事实+当前状态融合]

## Chapter 2: The Texture of Life (生活肌理)
[兴趣+消费+生活习惯串联，展示品味]

## Chapter 3: Interaction & Cognitive Protocol (交互协议)
### 3.1 沟通策略 (How to Speak)
### 3.2 决策逻辑 (How to Think)

## Chapter 4: Deep Insights & Evolution (深层洞察)
* **矛盾统一性**: [看似冲突但实则合理的特质]
* **演变轨迹**: [带时间的变化记录]
* **涌现特征**: 3-7 个核心特质标签
  - `TagName` - 简短注释
```

## 约束
- 总长度不超过 2000 字符
- 禁止过度推测（冷启动阶段保持克制，信息不足可以不填）
- 所有内容必须来自提供的场景数据
- 只输出 Persona 文档内容，不输出思考过程

请严格按上述模板格式输出，不要输出 Markdown 代码块修饰符。"""


# ============================================================
# User Prompt Builder
# ============================================================

def format_persona_prompt(
    mode: str,
    current_time: str,
    total_atoms: int,
    scene_count: int,
    changed_scene_count: int,
    changed_scenes_content: str,
    existing_persona: str | None = None,
    trigger_reason: str | None = None,
) -> str:
    """构建 L3 画像生成的 user prompt"""

    mode_label = "🆕 首次生成" if mode == "first" else "🔄 迭代更新"

    trigger_section = f"\n### 触发信息\n{trigger_reason}\n" if trigger_reason else ""

    existing_section = ""
    if existing_persona:
        existing_section = f"""
## 📄 当前 Persona
*以下是现有画像的完整内容（{len(existing_persona)} 字符），基于此更新后请控制在2000字内：*

{existing_persona}

---
"""

    iteration_guide = ""
    if mode == "incremental":
        iteration_guide = """
## 🔄 迭代决策指南
面对变化场景，自主判断处理方式：强化（佐证已有洞察）/ 补充（新维度）/ 修正（矛盾）/ 重构（结构调整）/ 不改（无有用新增内容）。
"""

    return f"""**⏰ 更新时间**: {current_time}
**模式**: {mode_label}
{trigger_section}
## 📊 统计
- **总记忆数**: {total_atoms} 条
- **场景总数**: {scene_count} 个
- **变化场景**: {changed_scene_count} 个（自上次更新后）

---

## 📄 变化场景完整内容
{changed_scenes_content or "（无变化场景）"}

{existing_section}
{iteration_guide}"""

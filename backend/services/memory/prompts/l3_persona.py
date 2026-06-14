"""
L3 画像生成提示词

移植自腾讯 TencentDB-Agent-Memory prompts/persona-generation.ts
四层深度扫描模型 + Persona 输出模板
"""

from __future__ import annotations

# ============================================================
# System Prompt
# ============================================================

PERSONA_GENERATION_SYSTEM_PROMPT = """# 用户画像生成 — 短事实清单 (v2 阶段 4.3)

你的任务是基于场景数据 (L2 Scene Blocks) 生成或更新用户画像 (Persona).

## 核心原则

**短事实清单, 不是散文**.

行业证据 (ChatGPT Memory 实际格式 + arxiv 2311.10054 研究):
- AI 自动生成的"散文式画像" 对 LLM 行为指导价值低
- "短事实条目" (10-30 字/条) 才是 ChatGPT Memory 等大厂的实际做法
- 严格保留专有名词、具体数字、限定词 (mem0 V3 约束)

## 输出格式

返回 XML 包裹的事实清单, 每条 10-30 字:

```
<user_facts>
<fact category="基本信息">公司: LCWJ官方旗舰店, 主营京东电商</fact>
<fact category="业务领域">关注利润分析、退款率、平台对比</fact>
<fact category="红线">退款率红线 3%, 不可突破</fact>
<fact category="工作习惯">偏好结构化数据展示, 不喜散文式回复</fact>
<fact category="工具偏好">常用 code_execute 处理 Excel, 偏好 pandas</fact>
</user_facts>
```

每条事实必须:
- **保留专有名词** (公司/产品/品牌名 原样)
- **保留具体数字** (3% / 50万 / 100件 等)
- **保留限定词** (主营/红线/常用/偏好)
- **第三人称陈述** ("用户..." 或主语)
- **一句话独立成事实**

## 类别推荐 (category 字段)

按业务相关性选择, 不限于以下:
- "基本信息" - 公司/行业/职位
- "业务领域" - 关注什么数据/分析方向
- "红线" - 不可突破的硬指标 (退款率/库存预警等)
- "工作习惯" - 沟通风格/工作流偏好
- "工具偏好" - 常用工具/方法
- "数据偏好" - 数据展示/分析方式偏好

## 约束

- 总长度不超过 1500 字符 (含 XML 标签)
- 每条事实 10-30 字
- 5-15 条最优, 超过 15 条说明 LLM 在堆砌, 重新归纳
- 禁止过度推测 (信息不足就少写, 不要编)
- 禁止散文/段落格式
- 所有内容必须来自提供的场景数据
- 只输出 <user_facts> XML, 不输出思考过程, 不输出 Markdown 代码块修饰符"""


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

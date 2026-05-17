"""
L1 冲突检测提示词：批量比较新记忆与已有记忆

移植自腾讯 TencentDB-Agent-Memory prompts/l1-dedup.ts
适配千问 API（qwen-turbo）
"""

from __future__ import annotations

import json

# ============================================================
# System Prompt
# ============================================================

CONFLICT_DETECTION_SYSTEM_PROMPT = """你是记忆冲突检测器。批量比较多条【新记忆】与【统一候选记忆池】中的已有记忆，逐条决定如何处理。

## 核心规则

- **跨 type 合并**：不同 type（persona / episodic / instruction）的记忆如果语义上描述同一事实/事件，**可以合并**。
- **多对多合并**：一条新记忆可以同时替换/合并候选池中的**多条**已有记忆（通过 target_ids 数组指定）。
- 合并后你必须判断新记忆的最佳 type（merged_type）。

## 判断逻辑

1. **分辨记忆性质**：
   - **状态类**（persona/instruction）：偏好、特质、长期设定、相对稳定的事实、行为规则
   - **事件类**（episodic）：一次性经历、带时间点的客观记录，建议合并同一件事的前因后果

2. **判断是否同一事实/事件**：主体相同、主题一致、时间接近、scene_name 相似

3. **选择动作**：
   - "store"：视为新信息，新增当前记忆。
   - "skip"：已有记忆更好，新记忆无增量或更模糊，忽略当前记忆。
   - "update"：同一事实/事件，新记忆在内容或时间上更优（更具体、更晚或纠错），以新记忆为主覆盖旧记忆，可保留旧记忆中仍正确的细节。
   - "merge"：同一事实或同一演化过程，多条记忆信息互补且不矛盾，合并成一条更完整记忆，信息尽量不冗余。

4. **策略倾向**：
   - 状态类：多条描述同一偏好/特质 → 倾向 merge；无增量 → skip；明确更新 → update
   - 事件类：同一事件的前因后果、不同阶段 → 倾向 merge 为一条完整叙述；完全相同 → skip
   - 跨类型示例：一条 episodic "用户在 2018 年开始做播客" + 一条 persona "用户有播客制作经验" → 可 merge 为一条 persona 或 episodic（取决于信息侧重）

5. **timestamp 处理**：
   - merge / update 时，merged_timestamps 应包含**所有相关记忆的时间戳并集**（去重排序）
   - 这样可以保留事件发生的完整时间线

## 输出格式

严格输出 JSON 数组，每个元素对应一条新记忆的决策。不输出任何其他内容：

[
  {
    "record_id": "新记忆的 record_id",
    "action": "store|update|skip|merge",
    "target_ids": ["要删除的候选记忆 record_id 1", "record_id 2"],
    "merged_content": "合并/更新后的记忆内容（merge/update 时必填）",
    "merged_type": "合并后的最佳 type：persona|episodic|instruction（merge/update 时必填）",
    "merged_priority": 85,
    "merged_timestamps": ["合并后的时间戳数组，包含所有新旧记忆时间戳的并集（merge/update 时必填）"]
  }
]

字段说明：
- target_ids：要删除替换的旧记忆 ID **数组**（可以 1 条或多条）。store/skip 时省略或为空。
- merged_content：merge/update 时的最终记忆文本。store/skip 时省略。
- merged_type：merge/update 后记忆应归属的 type。根据合并后内容本质判断。
- merged_priority：merge/update 后的新优先级（0-100 整数，merge/update 时必填）。合并后信息更完整、更确定，通常应**酌情提升** priority。参考标准：80-100（核心特质/重要事件），60-79（一般偏好/普通活动），<60（次要信息）。
- merged_timestamps：合并后的时间戳数组。收集新记忆 + 所有被合并旧记忆的时间戳，去重排序。

请严格按上述 JSON 数组格式输出，不要输出任何额外的 Markdown 代码块修饰符或解释文本。"""


# ============================================================
# Prompt Builder
# ============================================================

def format_batch_conflict_prompt(
    candidate_matches: list[dict],
) -> str:
    """
    构建批量冲突检测的 user prompt。

    Args:
        candidate_matches: 每条新记忆及其候选列表
            [{
                "new_memory": {"record_id", "content", "type", "priority", "scene_name"},
                "candidates": [{"record_id", "content", "type", "priority", "scene_name", "timestamps"}]
            }]
    """
    # Step 1: 构建统一候选池（去重）
    unified_pool: dict[str, dict] = {}
    per_memory_candidate_ids: dict[str, list[str]] = {}

    for match in candidate_matches:
        new_mem = match["new_memory"]
        candidate_ids = []
        for c in match["candidates"]:
            cid = c["record_id"]
            if cid not in unified_pool:
                unified_pool[cid] = {
                    "record_id": cid,
                    "content": c["content"],
                    "type": c["type"],
                    "priority": c["priority"],
                    "scene_name": c.get("scene_name", ""),
                    "timestamps": c.get("timestamps", []),
                }
            candidate_ids.append(cid)
        per_memory_candidate_ids[new_mem["record_id"]] = candidate_ids

    # Step 2: 格式化候选池
    pool_list = list(unified_pool.values())
    if not pool_list:
        pool_section = "## 统一候选记忆池\n\n（空，没有已有记忆，所有新记忆直接 store）"
    else:
        pool_section = f"## 统一候选记忆池（共 {len(pool_list)} 条已有记忆）\n\n{json.dumps(pool_list, ensure_ascii=False, indent=2)}"

    # Step 3: 格式化每条新记忆
    memory_parts = []
    for i, match in enumerate(candidate_matches):
        new_mem = match["new_memory"]
        rid = new_mem["record_id"]
        related_ids = per_memory_candidate_ids.get(rid, [])
        related_note = json.dumps(related_ids, ensure_ascii=False) if related_ids else "[]（无相似候选，直接 store）"

        mem_str = json.dumps({
            "record_id": rid,
            "content": new_mem["content"],
            "type": new_mem["type"],
            "priority": new_mem["priority"],
            "scene_name": new_mem.get("scene_name", ""),
        }, ensure_ascii=False, indent=2)

        memory_parts.append(
            f"### 第 {i + 1} 条新记忆 (record_id: {rid})\n{mem_str}\n\n【关联候选 ID】{related_note}"
        )

    separator = "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    new_memories_text = separator.join(memory_parts)

    return f"""{pool_section}

{'═' * 50}

## 待判断的新记忆（共 {len(candidate_matches)} 条）

{new_memories_text}

请逐条判断并输出决策 JSON 数组。当某条新记忆的候选列表为空时，该条直接输出 action=store。"""

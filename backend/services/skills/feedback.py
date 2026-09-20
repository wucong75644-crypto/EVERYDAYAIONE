"""Allowlisted public feedback, independent from model control results."""


def skill_step(result: dict, skills, *, step_id: str) -> dict:
    block = {"type": "skill_step", "step_id": step_id, "status": "error"}
    if result.get("ok") and skills is not None:
        candidate = skills.directory.get(result.get("skill_id"))
        if candidate is not None:
            return {**block, "status": "completed",
                    "name": candidate.catalog_metadata.name or candidate.skill_key,
                    "revision": candidate.revision}
    code = result.get("code", "")
    if code == "SKILL_RUNTIME_DISABLED":
        reason = "Skill 功能暂未开放，本次按普通聊天继续。"
    elif code in {"SKILL_NOT_AVAILABLE", "SKILL_ACCESS_DENIED", "SKILL_IDENTITY_UNAVAILABLE",
                  "SKILL_ACTOR_UNAVAILABLE", "SKILL_PINNED_REVISION_UNAVAILABLE"}:
        reason = "该 Skill 当前不可用或你暂无使用权限，请重新选择。"
    elif code == "SKILL_SELECTION_CHANGED":
        reason = "该 Skill 版本已更新，请重新选择后发送。"
    elif code.startswith("SKILL_ARGS") or code.startswith("SKILL_TEMPLATE"):
        reason = "该 Skill 需要补充参数，暂不支持直接启用。"
    else:
        reason = "暂时无法启用该 Skill，请稍后重试。"
    return {**block, "reason": reason}

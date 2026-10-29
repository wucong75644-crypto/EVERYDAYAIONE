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
    if code == 'SKILL_WORKFLOW_CONFLICT':
        names = [skills.directory[k].catalog_metadata.name or k for k in result.get('conflicting_skills', [])
                 if skills is not None and k in skills.directory]
        reason = '、'.join(names) + ' 对当前步骤使用不同制作流程。已保留原设置，请调整本次选择或管理会话固定方法。'
    elif code == 'SKILL_ACTION_BATCH_CONFLICT':
        reason = '当前批次混合了策划和图片提交，请按当前步骤分别执行。原设置已保留。'
    elif code == 'SKILL_DEPENDENCY_UNAVAILABLE':
        reason = '该方法依赖的其他方法当前不可用，已保留原设置，请检查选择。'
    elif code == 'SKILL_REQUIRED_CAPABILITY_UNAVAILABLE':
        reason = '该组合缺少当前步骤必需的能力，已保留原设置，请调整方法或检查权限。'
    elif code == 'SKILL_WORKFLOW_METHOD_UNAVAILABLE':
        reason = '该方法声明的执行方式尚不受支持，请检查方法配置。'
    elif code == 'SKILL_COMPOSITION_INCONSISTENT':
        reason = '原任务的方法状态无法核实，已停止自动提交，请查看原任务状态后继续。'
    elif code == "SKILL_NOT_APPLICABLE":
        reason = "当前请求不需要此策划方法，将按你的最新要求继续处理。"
    elif code == "SKILL_ROUTING_UNAVAILABLE":
        reason = "电商策划入口暂不可用，普通生图仍可继续。"
    elif code == "SKILL_RUNTIME_DISABLED":
        reason = "Skill 功能暂未开放，本次按普通聊天继续。"
    elif code in {"SKILL_NOT_AVAILABLE", "SKILL_ACCESS_DENIED", "SKILL_IDENTITY_UNAVAILABLE",
                  "SKILL_ACTOR_UNAVAILABLE", "SKILL_PINNED_REVISION_UNAVAILABLE"}:
        reason = "该 Skill 当前不可用或你暂无使用权限，请重新选择。"
    elif code == "SKILL_SELECTION_CHANGED":
        reason = "该 Skill 版本已更新，请重新选择后发送。"
    elif code == "SKILL_SESSION_REVISION_LOCKED":
        reason = "当前会话已固定该 Skill 的其他版本；本轮继续使用固定版本。如需更换，请先移除会话绑定。"
    elif code in {'SKILL_TEMPLATE_ARGS_SERVER_ONLY', 'SKILL_TEMPLATE_SERVER_VALUE_UNAVAILABLE',
                  'SKILL_TEMPLATE_VARIABLE_UNDECLARED', 'SKILL_TEMPLATE_VARIABLE_FORBIDDEN'}:
        reason = "该 Skill 的模板配置或任务上下文不可用，请联系管理员检查。"
    elif code == 'SKILL_ASSET_BUDGET_EXCEEDED':
        reason = "该 Skill 的附件内容超过当前容量，请联系管理员精简引用。"
    elif code.startswith("SKILL_ARGS") or code.startswith("SKILL_TEMPLATE"):
        reason = "该 Skill 需要补充参数，暂不支持直接启用。"
    else:
        reason = "暂时无法启用该 Skill，请稍后重试。"
    return {**block, "reason": reason}

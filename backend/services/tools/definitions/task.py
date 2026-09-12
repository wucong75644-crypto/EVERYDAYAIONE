"""task ToolSpec ownership. Business handlers are unchanged."""

from ..spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec


def _schema_manage_scheduled_task():
    return {
        "type": "function",
        "function": {
            "name": "manage_scheduled_task",
            "description": (
                "管理定时任务（自动执行重复性工作，如每日推送报表、定期数据同步）。\n\n"
                "Actions:\n"
                "- create: 传 description 自然语言描述任务和频率，只返回预填配置表单；此时任务尚未创建。"
                "用户提交表单后，系统会先规划工具路径并进行只读安全试跑；预检通过后必须再次确认，才会创建正式任务。\n"
                "- list: 查看当前任务列表。\n"
                "- update: 传 task_name + description 描述变更，返回表单供确认。\n"
                "- pause/resume/delete: 传 task_name（模糊匹配）或 task_id 定位任务。\n\n"
                "调用 create 后，回复只能说明“配置表单已生成，任务尚未创建”；"
                "不得宣称任务已创建、已启用或已开始执行，也不得补充与表单不一致的频率或时间。"
                "任务不存在时建议用 list 查看现有任务。"
                "不要用于：一次性数据查询 → erp_agent；手动触发执行 → 不支持。"
            ),
            "parameters": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "update", "pause", "resume", "delete"],
                        "description": "操作类型。create 需配合 description，其余需配合 task_name 或 task_id",
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "create/update 时传：自然语言描述任务内容和频率。"
                            "e.g. '每天早上9点推送销售日报'、'每周一上午10点生成库存周报'"
                        ),
                    },
                    "task_name": {
                        "type": "string",
                        "description": (
                            "任务名称，用于 update/pause/resume/delete。"
                            "e.g. '销售日报推送'"
                        ),
                    },
                    "task_id": {
                        "type": "string",
                        "description": "任务 ID，可传完整 UUID 或前 8 位短 ID",
                    },
                },
            },
        },
    }


def build_specs():
    return (
        ToolSpec(
            name='manage_scheduled_task', schema=_schema_manage_scheduled_task(),
            domain='general', availability=ToolAvailability(requires_org=True, requires_personal_context=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('proposal_or_form', 'task_definition'), executor_type="legacy", handler_key='manage_scheduled_task',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.task.build_specs", definition_kind="explicit",
            catalog_order=32, catalog_groups=('common_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=(),
            policy_rules=ToolPolicyRules(
                operation='proposal',
                plan_allowed=True,
                execution_modes=('interactive', 'scheduled'),
                action_rule='scheduled_task',
                required_permissions=(),
            ),
        ),
    )

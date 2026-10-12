"""task ToolSpec ownership. Business handlers are unchanged."""

from ..spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec


def _schema_manage_scheduled_task():
    from services.skills.selection import SkillSelection
    return {
        "type": "function",
        "function": {
            "name": "manage_scheduled_task",
            "description": (
                "创建、查看、修改、暂停、恢复、删除定时任务。\n"
                "create：直接把用户需求整理为 definition 对象，并用 recipient 指定收件对象。"
                "prompt 是每次实际执行的完整业务要求，保留数据日期、店铺、指标口径、分组、筛选和输出要求；"
                "不要包含创建任务、触发时间或发送动作。任务将独立运行，不要依赖当前聊天中的省略指代。"
                "只填写用户明确给出的安排；缺项就省略相应字段，由表单补齐，不能猜默认时间或频率。"
                "不要先执行一次业务查询。聊天入口不再用 description 做第二次模型解析。\n"
                "每N分钟/小时用interval和interval_minutes；条件提醒必须传 definition.notification_policy 对象，不能放在顶层、不能传JSON字符串，也不能只写进prompt。"
                "网页给自己时，系统自动把结果关联创建任务的原聊天。条件监测须在prompt保留采集对象和metric口径。\n"
                "创建时即使字段完整也必须先展示可编辑确认表单；用户点击确认前不规划、不提交、不创建任务。\n"
                "skills 可选，仅传已知且允许用于定时任务的 Skill 标识和固定版本，不猜标识；"
                "不需要 Skill 时省略。修改未传 skills 保留原绑定，传空数组表示移除绑定。\n"
                "update：用 task_id 和 definition 中的变更字段，只修改用户要求的项。"
                "只改输出形式用 output_format，不能重写原业务范围。未传字段保持不变。\n"
                "pause/resume/delete：只传 task_id；还不知道 ID 时用 list 或 task_name 查找，"
                "同名有歧义须请用户选择，禁止猜 ID。修改不能删除重建。\n"
                "list：查看已有任务和 ID。\n"
                "根据真实回执回复：表单表示待补充；检查中表示尚未生效；awaiting_approval 等待卡片确认；"
                "只有 applied 才能称已创建/修改/暂停/恢复/删除。创建表单确认后检查通过才可生效，普通明确修改按原规则处理，"
                "删除及需额外授权的变更必须确认。暂停仅停止后续自动执行，本次已开始的继续完成。"
                "仅本次查询用 erp_agent；立即运行已有任务请使用任务面板。"
            ),
            "parameters": {
                "type": "object",
                "required": ["action"],
                "additionalProperties": False,
                "properties": {
                    "definition": {
                        "type": "object",
                        "additionalProperties": False,
                        "description": "create 必须传对象（缺项可省略）；update 仅传变更字段。禁止传权限、执行策略或用户 ID。",
                        "properties": {
                            "skills": {"type": "array", "title": "固定 Skill", "maxItems": 4,
                                       "items": SkillSelection.model_json_schema()},
                            "name": {"type": "string", "title": "任务名称", "minLength": 1},
                            "prompt": {"type": "string", "title": "执行内容", "minLength": 1},
                            "schedule_type": {"type": "string", "title": "执行频率", "enum": ["once", "daily", "weekly", "monthly", "interval"]},
                            "interval_minutes": {"type": "integer", "title": "间隔分钟", "minimum": 1, "maximum": 1440, "description": "interval 必填。分钟间隔须整除60；小时间隔须整除24，如30、60、120、360。按时区整点对齐，不是从创建时刻起计时。"},
                            "notification_policy": {"type": "object", "title": "通知规则", "additionalProperties": False, "properties": {
                                "mode": {"type": "string", "enum": ["always", "conditional"]},
                                "metric": {"type": "string", "description": "要采集的数值字段，如 likes、comments_count、sales"},
                                "comparison": {"type": "string", "enum": ["value", "delta", "percent"]},
                                "threshold": {"type": "number", "minimum": 0, "maximum": 1000000000000},
                                "window_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
                                "repeat_increment": {"type": "number", "minimum": 0, "maximum": 1000000000000},
                                "cooldown_minutes": {"type": "integer", "minimum": 0, "maximum": 10080}
                            }, "description": "conditional 达到 >= threshold 才通知，无变化静默。delta/percent 必须指定比较窗口；首次建基线。默认同素材持续命中不重复；repeat_increment为较上次通知的原始指标新增量，必须>0，cooldown只是附加限制。明确使用用户要求，缺条件请追问；always 恢复每次通知。"},
                            "time_str": {"type": "string", "title": "执行时间", "pattern": "^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", "description": "周期任务 HH:MM，北京时间。每天八点为 08:00，不是从现在起间隔24小时。"},
                            "weekdays": {"type": "array", "title": "每周几", "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 0, "maximum": 6}, "description": "0为周日，1为周一，依次至6为周六。"},
                            "day_of_month": {"type": "integer", "title": "每月几号", "minimum": 1, "maximum": 31},
                            "run_at": {"type": "string", "title": "执行日期和时间", "description": "单次任务必须提供含时区的完整 ISO8601 日期时间，不猜日期。"},
                            "output_format": {"type": "string", "title": "输出格式", "enum": ["表格", "列表", "项目符号", "文字", "Markdown表格", "CSV"], "description": "只修改输出形式时使用；原业务要求保持。"},
                        },
                    },
                    "recipient": {"type": "string", "description": "用户指定的收件人/渠道原意，如 我、我（企微）、销售群、张三。不知道则省略，不能把指定群改为自己。后端解析真实身份并检查权限。"},
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "update", "pause", "resume", "delete"],
                        "description": "操作类型。create/update 配合 definition；update/pause/resume/delete 配合 task_id 或 task_name；list 无需其他字段。",
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "旧 description-only API 的兼容字段。新聊天调用必须传 definition，"
                            "本字段只能保留原始要求用于展示，不代替结构化执行内容。"
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
            name='manage_scheduled_task', capability='platform.manage_scheduled_task', schema=_schema_manage_scheduled_task(),
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

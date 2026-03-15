"""
企微模板卡片 JSON 工厂

构建各类型模板卡片的标准 JSON 结构，供 ws_client / command_handler 调用。
卡片类型：text_notice / button_interaction / multiple_interaction

企微协议约束：
- task_id：同一机器人不可重复，最长 128 字节，仅数字/字母/_-@
- button_list：最多 6 个按钮
- select_list：最多 3 个下拉框，每个最多 10 个选项
- horizontal_content_list：最多 6 条
"""

import uuid
from typing import List, Optional


def _gen_task_id() -> str:
    """生成唯一 task_id（格式：card_{hex32}，共 37 字节，远小于 128 上限）"""
    return f"card_{uuid.uuid4().hex}"


# ── 企微可用模型列表（卡片下拉框展示，最多 6 个常用模型） ──

WECOM_MODEL_OPTIONS: List[dict] = [
    {"id": "auto", "text": "智能模式（自动选模型）"},
    {"id": "deepseek-v3.2", "text": "DeepSeek V3.2"},
    {"id": "deepseek-r1", "text": "DeepSeek R1（推理）"},
    {"id": "gemini-3-pro", "text": "Gemini 3 Pro"},
    {"id": "qwen3.5-plus", "text": "通义千问 3.5"},
    {"id": "anthropic/claude-sonnet-4.6", "text": "Claude Sonnet 4.6"},
]


class WecomCardBuilder:
    """企微模板卡片 JSON 工厂"""

    @staticmethod
    def welcome_card() -> dict:
        """欢迎语卡片（enter_chat 事件触发）"""
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": "欢迎使用 AI 助手",
                "desc": "我可以帮你聊天、生成图片、生成视频，还能记住你的偏好",
            },
            "button_list": [
                {"text": "开始聊天", "style": 1, "key": "start_chat"},
                {"text": "查看功能", "style": 2, "key": "show_help"},
                {"text": "查积分", "style": 2, "key": "check_credits"},
            ],
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def help_card() -> dict:
        """功能菜单卡片"""
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": "AI 助手功能",
                "desc": "点击按钮快速使用，也可以直接打字发指令",
            },
            "sub_title_text": (
                "文字指令：查积分 | 我的记忆 | 新对话 | "
                "切换模型 | 深度思考 | 帮助"
            ),
            "button_list": [
                {"text": "查看积分", "style": 2, "key": "check_credits"},
                {"text": "管理记忆", "style": 2, "key": "manage_memory"},
                {"text": "切换模型", "style": 2, "key": "switch_model"},
                {"text": "新建对话", "style": 2, "key": "new_conversation"},
                {"text": "思考模式", "style": 2, "key": "toggle_thinking"},
            ],
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def credits_card(balance: int) -> dict:
        """积分余额卡片"""
        return {
            "card_type": "text_notice",
            "main_title": {
                "title": "积分余额",
                "desc": "当前可用积分",
            },
            "emphasis_content": {
                "title": str(balance),
                "desc": "积分",
            },
            "sub_title_text": "积分会在使用 AI 功能时自动扣除",
            "card_action": {"type": 0},
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def credits_insufficient_card(
        needed: int, balance: int, action: str
    ) -> dict:
        """积分不足卡片"""
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": "积分不足",
                "desc": f"生成{action}需要 {needed} 积分，当前余额 {balance}",
            },
            "button_list": [
                {"text": "查看积分详情", "style": 1, "key": "check_credits"},
            ],
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def memory_list_card(memories: list) -> dict:
        """记忆列表卡片

        Args:
            memories: 记忆条目列表，每条包含 memory 字段
        """
        total = len(memories)
        # horizontal_content_list 最多 6 条
        display = memories[:6]
        h_list = []
        for i, mem in enumerate(display, 1):
            text = mem.get("memory", "")
            # keyname 最多 5 字符，value 最多 30 字符
            h_list.append({
                "keyname": f"#{i}",
                "value": text[:28] + "…" if len(text) > 28 else text,
            })

        suffix = f"（显示前 6 条，共 {total} 条）" if total > 6 else ""
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": f"我的记忆（共 {total} 条）",
                "desc": f"AI 自动记住的你的偏好和信息{suffix}",
            },
            "horizontal_content_list": h_list,
            "button_list": [
                {"text": "清空所有记忆", "style": 4, "key": "clear_all_memory"},
            ],
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def memory_empty_card() -> dict:
        """空记忆卡片"""
        return {
            "card_type": "text_notice",
            "main_title": {
                "title": "暂无记忆",
                "desc": "和我多聊聊，我会自动记住你的偏好和重要信息",
            },
            "card_action": {"type": 0},
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def memory_cleared_card() -> dict:
        """记忆已清空确认卡片（更新卡片用）"""
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": "已清空所有记忆",
                "desc": "你的偏好信息已全部删除，重新和我聊天可以建立新记忆",
            },
            "button_list": [
                {"text": "好的", "style": 2, "key": "noop"},
            ],
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def model_select_card(
        models: Optional[List[dict]] = None,
        current_model: Optional[str] = None,
    ) -> dict:
        """模型选择卡片

        Args:
            models: 模型列表 [{"id": "...", "text": "..."}]，默认使用 WECOM_MODEL_OPTIONS
            current_model: 当前选中的模型 ID
        """
        options = models or WECOM_MODEL_OPTIONS
        # 最多 10 个选项
        option_list = [{"id": m["id"], "text": m["text"]} for m in options[:10]]

        select = {
            "question_key": "model_select",
            "title": "选择 AI 模型",
            "option_list": option_list,
        }
        if current_model:
            select["selected_id"] = current_model

        return {
            "card_type": "multiple_interaction",
            "main_title": {
                "title": "切换 AI 模型",
                "desc": f"当前模型：{current_model or '智能模式'}",
            },
            "select_list": [select],
            "submit_button": {"text": "切换", "key": "submit_model"},
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def model_switched_card(model_name: str) -> dict:
        """模型切换成功确认卡片（更新卡片用）"""
        return {
            "card_type": "text_notice",
            "main_title": {
                "title": "模型已切换",
                "desc": f"当前使用：{model_name}",
            },
            "sub_title_text": "接下来的对话将使用新模型回复",
            "card_action": {"type": 0},
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def thinking_mode_card(current_mode: str) -> dict:
        """深度思考模式卡片

        Args:
            current_mode: "deep" | "fast"
        """
        is_deep = current_mode == "deep"
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": "思考模式设置",
                "desc": f"当前模式：{'深度思考' if is_deep else '快速回复'}",
            },
            "button_list": [
                {
                    "text": "深度思考",
                    "style": 1 if is_deep else 2,
                    "key": "thinking_deep",
                },
                {
                    "text": "快速回复",
                    "style": 1 if not is_deep else 2,
                    "key": "thinking_fast",
                },
            ],
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def thinking_switched_card(mode: str) -> dict:
        """思考模式切换确认卡片（更新卡片用）"""
        label = "深度思考" if mode == "deep" else "快速回复"
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": f"已切换为{label}",
                "desc": "深度思考会更深入分析问题" if mode == "deep"
                else "快速回复会更快速地回答",
            },
            "button_list": [
                {"text": f"当前：{label}", "style": 1, "key": "noop"},
            ],
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def new_conversation_card() -> dict:
        """新对话确认卡片"""
        return {
            "card_type": "text_notice",
            "main_title": {
                "title": "已创建新对话",
                "desc": "之后的消息将在新对话中，之前的对话记录仍然保留",
            },
            "card_action": {"type": 0},
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def generation_done_card(media_type: str, prompt: str) -> dict:
        """图片/视频生成完成卡片

        Args:
            media_type: "图片" | "视频"
            prompt: 用户原始提示词
        """
        # desc 最多 44 字符
        desc = prompt[:40] + "…" if len(prompt) > 40 else prompt
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": f"{media_type}已生成",
                "desc": f"提示词：{desc}",
            },
            "button_list": [
                {"text": "满意", "style": 1, "key": "gen_confirm"},
                {"text": "重新生成", "style": 2, "key": "gen_retry"},
            ],
            "task_id": _gen_task_id(),
        }

    @staticmethod
    def generation_confirmed_card(media_type: str) -> dict:
        """生成确认卡片（更新卡片用）"""
        return {
            "card_type": "button_interaction",
            "main_title": {
                "title": f"{media_type}已确认",
                "desc": "感谢反馈！",
            },
            "button_list": [
                {"text": "已确认", "style": 1, "key": "noop"},
            ],
            "task_id": _gen_task_id(),
        }

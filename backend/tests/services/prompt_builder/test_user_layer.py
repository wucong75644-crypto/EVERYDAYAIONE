"""UserLayer 单元测试。"""

from __future__ import annotations

from services.prompt_builder.layers.user_layer import (
    UserLayer,
    UserMessageInput,
)


class TestUserLayer:
    def test_user_message_no_timestamp_prefix(self):
        """user 原话不再被加 [06-10 23:00] 时间戳前缀。"""
        inp = UserMessageInput(text="读取文件", attachments_as_system=True)
        out = UserLayer.render(inp)
        assert out.user_message["role"] == "user"
        assert out.user_message["content"] == "读取文件"
        # 不应含时间戳模式
        assert "[06-" not in out.user_message["content"]

    def test_attachments_as_system_separates_xml(self):
        """attachments_as_system=True 时, XML 走独立 system block。"""
        xml = '<attachments count="1"><file path="x.xlsx"/></attachments>'
        inp = UserMessageInput(
            text="读取文件",
            attachments_xml=xml,
            attachments_as_system=True,
        )
        out = UserLayer.render(inp)
        # user 消息保持纯净
        assert out.user_message["content"] == "读取文件"
        # XML 走 system block
        assert out.attachments_system_block == xml

    def test_attachments_as_user_appends_to_text(self):
        """attachments_as_system=False 时 (回滚路径), XML 附加到 user text。"""
        xml = '<attachments><file path="x.xlsx"/></attachments>'
        inp = UserMessageInput(
            text="读取文件",
            attachments_xml=xml,
            attachments_as_system=False,
        )
        out = UserLayer.render(inp)
        assert xml in out.user_message["content"]
        # 此时 attachments_system_block 应为 None
        assert out.attachments_system_block is None

    def test_image_urls_become_multimodal(self):
        """有 image_urls 时, content 变成 list 形式 (多模态)。"""
        inp = UserMessageInput(
            text="这是什么图",
            image_urls=["https://cdn.x.com/a.jpg"],
        )
        out = UserLayer.render(inp)
        content = out.user_message["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "这是什么图"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "https://cdn.x.com/a.jpg"

    def test_workspace_prompt_separate_block(self):
        """workspace_prompt 走独立 system block。"""
        inp = UserMessageInput(
            text="分析数据",
            workspace_prompt="工作区有 3 个 Excel 文件",
        )
        out = UserLayer.render(inp)
        assert out.workspace_system_block == "工作区有 3 个 Excel 文件"

    def test_attachments_xml_not_duplicated(self):
        """attachments_as_system=True 时, XML 不应在 user content 里。"""
        xml = '<attachments><file path="x.xlsx"/></attachments>'
        inp = UserMessageInput(
            text="读取",
            attachments_xml=xml,
            attachments_as_system=True,
        )
        out = UserLayer.render(inp)
        # XML 只能出现在 system block, 不能在 user content
        assert xml not in out.user_message["content"]

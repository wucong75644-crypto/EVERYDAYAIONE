"""
多模态工具返回结构

供文件类工具（file_search / 后续多模态工具）在命中图片时返回
`type="image"` 的结果，让 chat_handler 在下一轮自动注入 image_url 多模态块
给视觉模型。

`FileReadResult` 命名是历史遗留——file_read 工具已废弃，但类名保留作为
"文件→多模态返回"通用类型，跨 chat_handler / chat_tool_mixin / chat_generate_mixin
的图片注入逻辑统一识别此类型。
"""

from dataclasses import dataclass


@dataclass
class FileReadResult:
    """文件读取/搜索返回结果（支持文本和多模态图片）

    用法约定：
      - 普通文本文件：type="text", text=内容
      - PDF/Word/PPT/CSV 摘要：type="text", text=提取/摘要文本
      - 图片文件：type="image", text=元信息描述, image_url=CDN URL
        ↑ chat_handler 识别 type=="image" 后会在下一轮 messages 追加
          {"type": "image_url", "image_url": {"url": image_url}} 多模态块
    """

    type: str = "text"  # "text" | "image"
    text: str = ""      # 文本内容（始终有值，作为工具结果的可见摘要）
    image_url: str = "" # 图片 URL（仅 type="image" 时有值）

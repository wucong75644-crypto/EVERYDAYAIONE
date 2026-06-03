"""Chat 上下文构建子模块。

按职责拆分 ChatContextMixin 的实现：
- attachments: <attachments> XML 渲染 + 工作区文件提示
- knowledge: 知识库 similarity 过滤
- content_extractors: DB content 字段的图片/文本/OAI 消息提取
- history_loader: 对话历史加载（token 预算驱动）
- summary_manager: 对话摘要读写

ChatContextMixin 主类保留在 chat_context_mixin.py，方法委托到本包。
"""

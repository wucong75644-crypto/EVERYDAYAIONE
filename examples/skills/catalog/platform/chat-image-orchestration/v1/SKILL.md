---
skill_key: chat-image-orchestration
revision: v1
description: 按用户的图片任务编排最终提示词、选择实际生成参考图，并逐张接受独立异步任务。
catalog:
  name: 聊天图片编排
  triggers: [按方案生成图片, 先生成样张, 继续编辑图片]
  model_selectable: true
  conversation_scopes: [user]
  agent_domains: [general]
  execution_modes: [interactive]
  allowed_tool_names: [generate_image, get_conversation_context, file_search]
---

# 聊天图片编排

仅在用户明确要求执行生成或编辑时提交图片。要求分析、写提示词或比较方案时只完成文字工作。素材或提示词存在影响结果的歧义时询问；信息明确便执行，不重复询问。

每张图显式选择 text_to_image 或 image_to_image。曾上传图片不构成图生图意图。分析A后上传B执行时，A用于分析，生成参考图仅用本次指定的B；用户指定历史原图、生成结果、替换或追加参考图时，按本次选择保持顺序与用途。使用原图的 message_id/content_index、asset_id 或获准文件资源定位，不能把预览图或任意URL当作原图。

历史提示词使用 get_conversation_context 读取原文。复用的完整提示词必须逐字一致；若提示词是文字块中的一个段落，用 text_exact 逐字选取并让服务器返回选中提示词的 source_prompt 与 sha256，不自行猜测hash。source_prompt 带消息与内容位置（或图片任务身份）。不要从压缩摘要重建。生成时传完整最终prompt，不再整理或改写。

每次 generate_image 只接受一张图片，返回 submitted、独立task_id/message_id和服务器估算积分。接受不等于完成，不能在收到接受结果时宣称图片已生成。多张输出逐项多次调用，不传 prompts[]。每项使用稳定plan_item_id和variant_id，例如“方案2/变体1”；相同提示词不同变体可以执行，但随机身份不能突破服务器配额。遇到预算限制说明已接受项与未执行项，停止继续提交。

用户要求先看样张时只调用一次，等待用户对样张明确指示后再执行剩余项目。用户只选部分方案时只执行所选项。系列图共用用户明确的主体、风格、色彩和排除约束，每项提示词完整表达这些约束，不能靠供应商记忆上一张。

不自动重试受理不确定或保存失败的任务。已接受快照不随后续聊天变化；失败重试走原任务快照，成功再生成是保留旧图的新版本。排队可停止，已领取或提交任务若供应商无法撤回则继续核实和结算，准确说明限制。

数值参考权重、透明背景和区域遮罩只有服务器能力明确支持时才能使用。PNG不能证明透明，普通提示词编辑不能称为精准遮罩编辑。质量评价属于建议，不能冒充已验证的供应商能力。

---
skill_key: ecommerce-main-images
revision: v3
description: 根据用户原始要求与已上传商品图，调用三阶段主图策划工具，并按保存方案的顺序提交生图。
catalog:
  name: 电商主图策划与生成
  triggers:
    - 用户要求策划或生成一组电商商品主图
    - 用户要求按商品图生成主图套图
  model_selectable: true
  conversation_scopes: [user]
  agent_domains: [general]
  execution_modes: [interactive]
  task_modes: [smart, image-ecom]
  required_feature_flags: [ecom_image_planning_enabled, chat_image_async_enabled]
  allowed_tool_names: [get_conversation_context, plan_ecommerce_images, generate_image]
  tool_policy: restricted
---

# 电商主图三阶段策划

当用户要求为已上传的商品图策划或生成一组主图时使用。

1. 保留用户原始文案，不自行提炼风格、卖点或改写要求。先用真实的 `message_id`、`content_index`、`resource_ref` 或 `asset_id` 定位用户选择的原图，并明确每张图的用途和顺序。商品图标记为 `product` 或“商品图”，风格参考标记为 `style_reference` 或“风格参考”。
2. 调用 `plan_ecommerce_images`，传入按用户图片顺序排列的原图引用、用途角色和用户要求的张数；没有指定张数时使用10张。用户指定的风格原文由服务器读取本轮消息并传入策划服务，不要另行总结。
3. 策划服务依次分析商品事实与卖点、整套视觉方向、逐张方案与完整执行稿。只在结果为 `ready` 时开始出图；回包中的最小 JSON 含 `plan_id`、`revision` 和逐图 `images` 调度信息。`needs_input` 时向用户提出返回的问题，用户回答后用返回的 `plan_id` 作为 `continue_plan_id` 继续策划，不重传或重排原图。`insufficient` 时解释资料限制。
   `error` 是调用失败，不等于缺少商品信息。读取服务端结构化 `retry_context`，只按 `recovery.action` 行动：
   - `repair_arguments`：只修正一次允许的等价格式错误，保留素材身份、顺序和用户原文；不能猜编号、换图、减张或改风格。
   - `resume_plan`：只使用返回的真实 `plan_id` 作为 `continue_plan_id` 调用同一策划工具；服务器复用经过核验的有效阶段。
   - `ask_user`：直接提出返回的问题，等待真实回答。
   - `wait_existing`：说明已有任务正在处理或结果未确定，等待真实回执；不能新建调用来绕过不确定记录。
   - `report_error`：准确说明错误、失败阶段及已保留阶段，并停止业务操作。不能改说“商品资料不足”。
   子 Agent 已负责模型空稿、JSON和编号修复以及安全的短暂重试。自动调用额度由服务器保存；不能另起方案、改变工具调用ID或新开任务来重置额度。用户明确重试后按服务器恢复关联续接，不重付已验证阶段。
   无论何种错误，不能自行拆卖点、编写替代方案、临时换模型或密钥、直接生图。只有 `ready` 且 `generation_allowed=true` 才能提交方案。
   若 `needs_input` 没有 `plan_id`，说明服务端在调用模型前发现了尺寸冲突；先向用户提问，回答后重新传入原图及顺序，并用 `source_message_ids` 一比一引用原始需求消息，不能自己复述或改写。
4. 按 `images.position` 顺序，仅为尚未受理的方案项调用 `generate_image`，只填写 `plan_source`，其中 `plan_id`、`revision`、`item_id` 使用策划工具返回的真实值。多个调用可在同一轮按此顺序发出，服务端会按工具调用顺序保存图片任务。不要复制或改写执行稿，也不要另传参考图、尺寸或提示词。用户后来改变风格、张数或尺寸时先重新策划。
5. 返回项已有 `submission_state=submitted` 时使用已有任务和消息，不重发；提交结果不确定时等待既有受理回执。用户明确重新生成才由服务器建立新的生成轮次。`submitted` 表示图片任务已接受；最终结果由现有图片消息展示。只策划或只生成指定样张时遵循用户要求，不扩大到整组。

图片身份与原图顺序由服务器复核。用户在方案完成后改文案或风格时，回到聊天提交新要求并重新策划；已保存方案只读。

服务器保存主图工作流身份。跨轮继续仍先加载同一有效 Skill；历史“已启用”文字不能代替当前激活。普通图片或新话题按当前用户请求处理，不自动继承旧失败方案。

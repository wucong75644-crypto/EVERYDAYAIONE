---
skill_key: chat-image-orchestration
revision: v3
description: 执行用户已选中的方案对象：按条目和版本提交图片，完整原文由后台逐字传递，参考图与规格沿用已登记绑定。
catalog:
  name: 聊天图片编排
  triggers: [执行已登记图片方案, 按方案对象生成图片, 先生成一张样张, 执行全部方案]
  model_selectable: true
  conversation_scopes: [user]
  agent_domains: [general]
  execution_modes: [interactive]
  allowed_tool_names: [generate_image, file_search]
  content_protocol: content-objects-v2
  coordination:
    version: 1
    kind: workflow
    claims:
      - scope: image.submission
        method: image.direct
        actions: [execute_prompt, edit]
---

# 聊天图片编排

本方法只承接已经登记的方案对象执行图片，不负责重新策划、重写提示词或登记修订。用户只要求写方案、改第二条、分析或比较时，不启动图片执行；不适用时退出本方法，由平台的内容产出流程完成登记和展示。

## 选择对象

使用宿主给出的 content_objects 目录，按用户所指标题、序号和当前版本选择。需要继续上轮任务且宿主给出 previous_content_objects 时，沿平台既有接续机制接回原任务，再选择对象。不能猜测成果身份或从图片来源编号寻找正文。

用户说第四条就选择第四条，只要求一张就只执行一项；要求全部则逐项执行，每项一次。用户修改第二条后，使用目录中的新成果版本，不能重新执行旧版本。不能通过新建一份方案、重命名条目或随机变体身份绕过已受理操作。

## 执行交接

调用 generate_image 只提供 content_source，其中 artifact_id、sha256 和 item_index 原样取自宿主目录。后台把该条保存的完整body原封不动放入prompt，绑定已保存的执行素材和输出规格；不附加共同要求、旧限制或其他文字。

不读取并摘取某段提示词，不传 prompt、source_prompt、chat_input、排除列表或自行编造的哈希；不删减、改写、翻译执行内容，也不重新拼接限制条件。没有对象时不能退回旧摘录路径：先退出本方法，让平台完成内容登记；确有对象歧义时询问具体对象，不要求用户重述已经明确的第四条等选择。

对象中仅分析素材不作为生成参考；执行素材仍受当前用户用途、权限和原文件核验约束。用户要换原图、修改完整原文或尺寸时，先让平台登记新版本，再执行新版本；不在调用时覆盖对象字段。

## 结果与异常

使用服务器默认图片能力，能力、规格与估算积分以当前宿主和原生回执为准，不选择或切换供应商模型。不支持的精确尺寸或其他规格须说明可用选项并等待用户决定，不静默近似或删除要求。

submitted 仅表示受理，不代表已生成。按真实 task_id/message_id 接收结果；结果未到不重复提交，不反复轮询模型。状态不明或保存失败先核实原任务，不能换调用身份重发。已受理快照不随后续内容修改，保留原任务和成果。

先看样张只提交一项，等用户反馈再执行其他项。预算或权限拒绝时保留已接受项和未执行项，如实说明；不能移除参考图、降低约束或改用纯文生图购买替代结果。

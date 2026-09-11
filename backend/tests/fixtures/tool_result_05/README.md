# 板块 05 的原协议样本

18 个 JSON 文件来自未修改的 `6c0737ab78f2d0cb3b2a8376498e7825b0829431`（板块 04 已验收 main），不是从 05 新实现生成。

复现入口：`bash docs/document/tool-unification-evidence/capture-05-main.sh`。脚本导出该提交的 backend 到新的临时目录，只复制测试驱动；`TOOL05_RECORD_GOLDEN=6c0737ab` 记录模式还验证旧 result.py 的 SHA256，不能用 05 的 ToolResult 实现生成替代快照。原执行结果见 `05-golden-main.txt`。

测试驱动 `test_tool_result_consumption.py::chat_run` 使用真实 ChatToolMixin、ToolRuntime/Policy/Dispatcher、apply_tool_results、emit 转换、两个原 sink 以及 checkpoint 构造器。身份 DB、业务 IO、delivery DB 和 WebSocket 运输使用可记录替身，没有调用真实外部服务。

- 文件包括原 WebSocket 消息完整字段、Actor delivery 事件以及 checkpoint；列表顺序逐项比较，不排序事件或删除字段。
- call/task/conversation/message/form/session/stream ID 和 execution_attempt 是固定测试输入，原值保留；delivery_seq 完整保留。
- 只归一化 WebSocket 外层或 tool 模型消息的 timestamp：正整数毫秒变为 `<UNIX_MILLISECONDS>`，通过 ISO 解析的字符串变为 `<ISO_TIMESTAMP>`。
- 只归一化 `type=tool_step` 的 elapsed_ms：先验证非负整数，再变为 `<NONNEGATIVE_ELAPSED_MS>`。业务数据中同名字段不归一化。
- 图片成功/失败、retry_context、下载/预览/原图 URL、文件路径、大小、mime_type 和表单字段原样比较。示例域名不代表在线可下载资源。

媒体 JSON 是离线协议夹具，用于覆盖 emit 转换字段。另有 `test_real_media_handler_with_existing_offline_provider_samples` 复用旧媒体测试的 MockImageResult/MockVideoResult、cat.png/demo.mp4 样本，真实运行媒体 Handler 的成功/失败及原扣费/退款调用结构（账务/Provider 均 mock）。当前真实 generate_video Handler 只返回带 URL 的 summary，没有 video emit；本块没有新增视频 block 或异步任务协议。人工播放、真实生成质量和生产 URL 有效性尚待用户验收，不由这些样本证明。

# KIE 默认图片模型升级（2026-09-30）

默认使用 GPT Image 2.5 Flare：文生图为 `gpt-image-2-5-flare-text-to-image`，图生图为 `gpt-image-2-5-flare-image-to-image`。覆盖智能图片路由、媒体工具、电商图批量生成与图片 Agent 默认配置；前端模型列表和订阅白名单同步登记。

KIE 仍使用 `POST /api/v1/jobs/createTask`、原有任务查询和回调协议。复用 `prompt`、`aspect_ratio`、`resolution`、`input_urls` 输入字段，不启用可选的背景控制。图生图最多 16 张参考图。当前产品接入已有宽高比中官方枚举支持的 9 种；不增加新的比例选项。2.5 的 auto、1:1 可使用 4K，不再套用 2.0 的降分辨率规则。

用户价格保持每张 1K=6、2K=10、4K=16 积分；2.5 的 KIE 成本记录按当前官方报价同步为 6/10/16。保留 GPT Image 2.0 的注册、路由、价格和输入行为，确保历史任务查询与重试仍可使用原模型。已保存的显式 2.0 选择和 `IMAGE_AGENT_KIE_MODEL` / `IMAGE_AGENT_KIE_I2I_MODEL` 环境覆盖不会自动迁移。

官方依据：

- [模型与价格](https://kie.ai/gpt-image-2-5)
- [Flare 文生图 API](https://docs.kie.ai/market/gpt/gpt-image-2-5-flare-text-to-image)
- [Flare 图生图 API](https://docs.kie.ai/market/gpt/gpt-image-2-5-flare-image-to-image)

验证使用模拟 KIE 的请求边界，检查实际模型 ID、参考图字段、4K 参数、积分与旧任务兼容；不调用收费生图 API。生产生图效果需要部署后验证。

本地验证：198 项后端定向测试、36 项前端测试通过；TypeScript 编译检查、变更文件 ESLint 和 `git diff --check` 通过。

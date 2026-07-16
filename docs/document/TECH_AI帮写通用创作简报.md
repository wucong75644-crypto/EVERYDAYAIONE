# AI 帮写通用创作简报技术设计

> 版本：v1.0
> 日期：2026-07-15
> 状态：技术方案已确认
> 任务等级：A级
> 前置文档：`UI_主图详情制作页面.md`、`TECH_主图详情页真实上传与草稿恢复.md`

## 一、目标与范围

### 1.1 目标

为主图详情制作页提供真实的“AI 帮写”能力。系统结合产品图、参考图、用户文字和平台设置，一次生成三套可选择、可编辑的整组通用创作简报。

该能力采用共享核心服务和入口适配器架构。第一阶段接入主图详情页，后续聊天窗口通过独立入口复用相同的 Prompt、Schema、模型调用、输出校验和失败降级能力。

### 1.2 本阶段包含

- 产品图必填，参考图可选。
- 支持仅参考图、仅用户文字、参考图与用户文字融合三种模式。
- 一次多模态调用返回卖点直达型、场景氛围型、视觉创意型三套通用简报。
- 展示产品事实、参考图解读和输入冲突处理结果。
- 用户可切换并分别编辑三套方案。
- 确认后将最终简报写入现有 `requirement` 字段。
- 主模型超时或失败时调用备用模型。
- 免费调用但增加用户级频率限制和结构化日志。

### 1.3 本阶段不包含

- 聊天窗口 UI 接入。
- 真实“分析产品”接口。
- 真实图片生成、积分结算和任务恢复。
- 三套候选方案持久化。
- 数据库表或字段变更。

## 二、已确认业务规则

### 2.1 两个 AI 动作的职责

1. AI 帮写是可选动作，生成整组图片共同遵守的通用创作简报。
2. 分析产品是后续必经动作，将用户确认的简报拆成 N 张逐图规划和 Prompt。
3. 确认生成后，N 张图片使用 N 次独立生图 API 调用。

### 2.2 输入优先级

```text
产品真实信息 > 用户明确要求 > 平台规范 > 参考图视觉特征 > AI 自主发挥
```

### 2.3 参考图职责

参考图只定义画面如何设计，包括背景、场景、构图、布局、色彩、光线、质感、排版和详情页内容节奏。

参考图不能覆盖产品事实，不能向最终结果引入参考商品、品牌、Logo、商标或原有文案，也不能改变真实产品的外形、包装、颜色和结构。

参考图既参与 AI 分析，也在后续真实生图阶段作为视觉输入。逐图规划必须为每张任务选择相关参考图，不能把全部参考图无差别发送给每个任务。

### 2.4 多图生成契约

```text
AI 帮写：1 次多模态调用 → 3 套通用创作简报
分析产品：1 次多模态调用 → N 张逐图规划和 Prompt
确认生成：N 次生图调用 → N 张图片
```

真实生成阶段使用受控并发，建议并发上限为 3。每张任务具有独立 Prompt、产品图选择、参考图选择、状态、重试和积分结算。

## 三、总体架构

```text
主图详情页入口                          聊天窗口入口（后续）
     │                                       │
详情项目输入适配器                       聊天附件输入适配器
     │                                       │
     └────────── RequirementAssistInput ─────┘
                         │
            RequirementAssistService
                         │
          多模态模型 + 结构化输出校验
                         │
              三套通用创作简报
```

入口适配器只负责鉴权、资产解析和表单标准化。核心 Service 不依赖详情页面或聊天组件。

## 四、项目上下文

### 4.1 架构现状

- `/detail-page` 使用独立 Zustand Store 管理上传、草稿和五步状态。
- 详情项目图片以 Workspace 路径引用保存，后端已实现用户、组织和文件归属校验。
- `/ecom-image/enhance-prompt` 已支持产品图和风格参考图的多模态分析，但职责是直接生成逐图规划。
- `EcomImageHandler` 已能将产品图和参考图交给图生图模型，但当前参考图分配粒度较粗。
- 通用 `Modal` 已基于 Radix Dialog，具备焦点锁定、ESC、遮罩关闭和无障碍能力。

### 4.2 可复用模块

- 详情项目和图片归属校验。
- Workspace、OSS 原图和缩略图解析。
- `DashScopeChatAdapter` 多模态调用。
- 现有主模型、备用模型和超时配置。
- 通用 API 响应、认证、日志及错误处理。
- `Modal`、`Button` 和详情页现有表单组件。
- `saveDetailSettings()` 与 `updateForm()` 的草稿保存链路。

### 4.3 设计约束

- 前端不能提交任意图片 URL，只能提交受控业务来源。
- 三套候选方案不写数据库，最终确认简报复用 `requirement`。
- 用户文本只能进入 user content，不能拼入 system 指令区域。
- TypeScript 禁止 `any`，异步请求必须处理取消和过期响应。
- 日志必须包含 `user_id`、`org_id`、`source_type` 和 `source_id`。
- 新文件不得超过 500 行，函数不得超过 120 行。

### 4.4 潜在冲突

- 旧 `/ecom-image/enhance-prompt` 接收前端 URL，不能直接作为详情页安全入口。
- 旧接口返回逐图规划，与三套通用简报职责不同。
- `image_ecom.py` 已有 373 行，不继续加入新接口。
- `useDetailPageStore.ts` 已有 318 行，弹窗临时状态使用独立 Hook。
- 现有 UI 文档只描述按钮 Loading 后直接回填，实施前需要同步为三套方案弹窗。

## 五、标准输入协议

核心 Service 接收标准化输入：

```text
RequirementAssistInput
├── user_id
├── org_id
├── source_type
├── source_id
├── product_images[]
│   ├── id
│   ├── original_url
│   └── display_name
├── reference_images[]
│   ├── id
│   ├── original_url
│   └── display_name
├── content_type
├── platform
├── language
├── aspect_ratio
├── quality
├── image_count
└── user_requirement
```

第一阶段 `source_type=detail_project`。未来增加聊天附件适配器时不修改核心 Service。

## 六、API 设计

### 6.1 生成三套简报

```http
POST /api/ecom-image/requirement-suggestions
```

请求示例：

```json
{
  "source": {
    "type": "detail_project",
    "project_id": "3b4a1ccb-ea6c-49ee-9d48-b720e2462ce1"
  },
  "settings": {
    "content_type": "main_image",
    "platform": "taobao",
    "language": "zh-CN",
    "aspect_ratio": "1:1",
    "quality": "1k",
    "image_count": 5,
    "requirement": "希望画面清新自然，突出400页大容量"
  }
}
```

前端发送表单快照，避免 500ms 防抖保存导致服务端读取旧设置。图片由后端根据项目读取，前端不提交 URL。

成功响应核心结构：

```json
{
  "success": true,
  "data": {
    "product_facts": {
      "product_name": "风景系列活页笔记本/讲义本",
      "confirmed_attributes": ["多款风景封面", "活页装订"],
      "unclear_items": ["纸张克重", "产品尺寸"]
    },
    "reference_analyses": [
      {
        "image_id": "reference-image-id",
        "primary_uses": ["background", "composition"],
        "summary": "浅色自然背景，主体偏右，左侧保留文案空间",
        "excluded_elements": ["参考商品", "品牌", "Logo", "原有文字"]
      }
    ],
    "conflicts": [],
    "suggestions": [
      {
        "id": "selling_point",
        "name": "卖点直达型",
        "style_name": "清新自然商品展示风",
        "brief_markdown": "完整可编辑通用创作简报"
      },
      {
        "id": "scene",
        "name": "场景氛围型",
        "style_name": "治愈系自然美学风",
        "brief_markdown": "完整可编辑通用创作简报"
      },
      {
        "id": "creative",
        "name": "视觉创意型",
        "style_name": "四季风景叙事风",
        "brief_markdown": "完整可编辑通用创作简报"
      }
    ]
  },
  "error": null,
  "meta": {
    "model": "实际模型",
    "fallback_used": false,
    "latency_ms": 8420,
    "project_version": 5
  }
}
```

### 6.2 错误码

| 错误码 | 场景 |
|---|---|
| `DETAIL_PROJECT_NOT_FOUND` | 项目不存在或无权限 |
| `DETAIL_PRODUCT_IMAGE_REQUIRED` | 没有有效产品图 |
| `DETAIL_IMAGE_NOT_READY` | 图片上传中或已失效 |
| `REQUIREMENT_ASSIST_RATE_LIMITED` | 调用过于频繁 |
| `REQUIREMENT_ASSIST_TIMEOUT` | 主模型和备用模型均超时 |
| `REQUIREMENT_ASSIST_INVALID_OUTPUT` | 输出无法通过 Schema |
| `REQUIREMENT_ASSIST_UNAVAILABLE` | 模型服务不可用 |

## 七、输出结构与校验

接口同时返回结构化信息和 `brief_markdown`：

- 结构化信息保证三套方案共享同一产品事实，并用于展示参考图解读和冲突处理。
- `brief_markdown` 用于用户编辑、确认后保存和后续分析产品。

服务端约束：

- `suggestions` 必须正好三项。
- 三个 `id` 不重复。
- `brief_markdown` 不为空且单份不超过 4000 字。
- 图片 ID 必须来自输入图片集合。
- 不允许模型返回或生成新的图片 URL。
- 无法确认的产品事实必须进入 `unclear_items`，不能作为营销事实输出。

## 八、Prompt 分层

### 8.1 System 层

- 定义角色为电商视觉策略师。
- 当前任务只生成通用创作简报，不生成逐图 Prompt，不触发生图。
- 区分产品事实与营销建议。
- 三套方案共享产品事实。
- 禁止复制参考图商品、品牌、Logo 和文字。
- 只输出符合 Schema 的 JSON。

### 8.2 Context 层

注入内容类型、平台、语言、比例、清晰度、数量、图片角色和用户原始需求。

### 8.3 图片角色层

```text
Image 1-N：产品图，只用于确认真实产品外观和信息。
Image N+1-M：视觉参考图，只用于背景、构图、色彩、光线、质感和排版。
```

### 8.4 三套差异约束

| 方案 | 重点 |
|---|---|
| 卖点直达型 | 核心功能、参数、购买理由和信息效率 |
| 场景氛围型 | 使用场景、目标人群和情绪价值 |
| 视觉创意型 | 视觉记忆点、叙事构图和差异化配色 |

## 九、前端状态与交互

候选状态放入独立 `useDetailRequirementAssist` Hook：

```text
idle → loading → success | error
```

状态包含弹窗开关、请求状态、返回结果、当前方案、三套编辑文本、错误和请求版本。

交互规则：

- 没有产品图或图片仍在上传时禁止调用。
- 弹窗打开后立即开始分析。
- 三套方案切换时分别保留编辑结果。
- 重新帮写期间保留旧结果，成功后才整体替换。
- 重新帮写失败时继续展示旧结果。
- 确认选择后调用现有 `updateForm({ requirement })`。
- 关闭弹窗和页面卸载时取消请求或忽略过期响应。

## 十、超时、降级和并发

- 总请求预算 100 秒。
- 主模型最多使用 60 秒；若耗尽主模型预算，备用模型最多使用剩余 40 秒。
- 主模型失败或超时后，备用模型使用剩余预算。
- 解析顺序：直接 JSON → 提取 JSON 块 → Pydantic 校验 → 安全缺省补齐。
- 两个模型均失败时返回可重试错误，不使用固定假方案冒充 AI 结果。
- 请求期间禁止重复提交。
- 新请求取消旧 `AbortController`，并使用递增 request ID 防止旧响应覆盖新状态。

## 十一、安全与可观测性

### 11.1 安全

- 不接受任意外部图片 URL。
- 校验当前用户、组织和业务来源归属。
- Workspace 路径继续使用安全解析。
- 用户文本不进入 system 指令。
- 返回内容按普通文本或安全 Markdown 渲染，不执行 HTML。
- 日志不记录完整签名 URL、完整用户需求、供应商原始错误或内部路径。
- 免费调用增加用户级频率限制。

### 11.2 日志

每次调用记录：用户、组织、来源类型、来源 ID、产品图和参考图数量、用户文字长度、模型、是否降级、耗时、解析结果、方案数量和错误码。

第一阶段不新增日志表，使用现有结构化日志和错误监控。

## 十二、边界场景

| 场景 | 处理策略 |
|---|---|
| 无产品图 | 禁止调用并提示上传 |
| 图片仍在上传 | 等待关联完成 |
| 无参考图、无用户文字 | 根据产品图和平台生成三套方向 |
| 只有参考图 | 参考图作为主要视觉方向 |
| 只有用户文字 | 用户要求和平台规范作为主要方向 |
| 两者都有 | 按固定优先级融合并输出冲突说明 |
| 图片引用失效 | 返回明确错误并提示重新上传或移除 |
| 快速连续点击 | 单有效请求 + request ID |
| 关闭弹窗 | 取消请求或忽略返回 |
| 主模型超时 | 调用备用模型 |
| 输出不可解析 | 返回可重试错误 |
| 重新帮写失败 | 保留旧三套方案 |
| Token 失效或越权 | 使用统一认证错误，不泄露资源状态 |
| 参考图互相冲突 | 输出逐张解读和最终取舍 |
| 用户要求与参考图冲突 | 用户明确要求优先并展示处理结果 |

## 十三、文件设计

### 13.1 新增后端文件

- `backend/schemas/ecom_requirement.py`：请求、标准输入和响应 Schema。
- `backend/services/agent/image/requirement_assist_service.py`：共享 AI 帮写核心服务。
- `backend/services/agent/image/requirement_assist_prompts.py`：专用 Prompt。
- `backend/services/agent/image/input_adapters.py`：详情项目输入适配，后续增加聊天适配。
- `backend/api/routes/ecom_requirement.py`：薄路由和统一响应。
- 对应后端测试文件。

### 13.2 修改后端文件

- `backend/main.py`：注册新路由。
- 详情项目 Service：仅在缺少安全读取 AI 输入图片的方法时增加最小方法。

### 13.3 新增前端文件

- `frontend/src/types/ecomRequirement.ts`：页面和未来聊天共用类型。
- `frontend/src/services/ecomRequirement.ts`：API 客户端。
- `frontend/src/hooks/useDetailRequirementAssist.ts`：页面入口适配和局部状态。
- `frontend/src/components/detail-page/RequirementAssistModal.tsx`：弹窗展示和编辑。
- 对应前端测试文件。

### 13.4 修改前端文件

- `GenerationSettings.tsx`：固定文案行为改为事件回调。
- `DetailPage.tsx`：连接 Hook、弹窗和最终 `updateForm()`。

## 十四、依赖、部署与回滚

### 14.1 依赖

无需新增前端或后端依赖。

### 14.2 数据库

无迁移，无回滚 SQL。

### 14.3 API 兼容

新增接口，不修改 `/ecom-image/enhance-prompt`，不影响现有 Chat 电商图模式。

### 14.4 回滚

1. 回滚前端弹窗接入提交。
2. 回滚后端新路由注册和相关文件。
3. 重启后端并重新部署前端。
4. 原有上传、草稿和手动填写需求继续可用。

## 十五、测试范围

### 15.1 后端

- 三种输入模式。
- 项目越权、产品图缺失、图片状态失效。
- 主模型成功、备用模型降级、双模型超时。
- 非 JSON、方案不足、非法图片 ID 和超长简报。
- 频率限制和统一错误响应。

### 15.2 前端

- 无产品图时禁用。
- 加载、成功、错误和重试状态。
- 三套方案切换和独立编辑。
- 确认后写入 requirement。
- 关闭取消、卸载 cleanup 和过期响应隔离。
- 重新帮写成功替换、失败保留旧结果。
- 弹窗键盘和焦点行为。

## 十六、实施顺序

### Phase 1：真实模型 POC

- 用真实产品图和参考图覆盖三种输入模式。
- 验证三套方案差异度、事实幻觉率和参考图理解。
- 记录响应时间、输出稳定性和模型成本。

POC 结论（2026-07-15）：

- `qwen-vl-max` 使用现有 DashScope 适配器可直接接收本地图片编码后的多模态消息，三种模式均一次返回 3 套合法结构。
- 第二轮耗时分别为 33.875 秒、36.694 秒、36.206 秒，结构校验、参考图分析和用户要求保留均通过。
- 使用“笔记本产品图 + 拼豆收纳参考图”进行污染测试，三套最终简报未复制参考商品名，证明图片角色标记基本有效。
- 产品图显示 200 页、用户要求 400 页时，模型能识别并报告冲突，但即使强化 Prompt，仍可能给出“暗示容量加倍”“模拟厚度”“替代卖点”等误导建议。
- 因此 Phase 2 必须增加确定性事实冲突闸门：冲突字段只进入 `conflicts/unclear_items`，从可执行简报字段中删除或替换为“待确认”；不能把模型原文未经校验直接交给前端或下游生图。

### Phase 2：后端核心能力

- Schema、Prompt、输入适配器、共享 Service、路由、限流、日志和测试。

### Phase 3：前端弹窗

- 公共类型、API、Hook、弹窗、按钮接入和测试。

完成状态（2026-07-16）：

- 已完成页面接入；仅在草稿存在、至少一张产品图就绪且没有上传/关联中的图片时允许调用。
- 选中方案只回填当前 `requirement` 并沿用现有草稿保存逻辑，不自动触发“分析产品”。
- API 请求、弹窗关闭和组件卸载均支持取消，过期响应不会覆盖当前状态。

### Phase 4：联调验证

- 三种模式、冲突、多标签页、请求取消、超时和降级端到端验证。

### 后续阶段

- 聊天入口适配器。
- 真实分析产品公共 Service。
- 逐图参考图选择协议。
- 真实生图、受控并发、任务恢复和积分结算。

## 十七、设计自检

- [x] 项目上下文、可复用模块、设计约束和潜在冲突已确认。
- [x] 需求和多角色方案评审已完成。
- [x] 入口适配与共享核心 Service 边界清晰。
- [x] 空值、超时、竞态、权限、取消和降级均有策略。
- [x] 无数据库迁移和新增依赖。
- [x] 新文件职责单一，预估不超过 500 行。
- [x] API 向后兼容，支持独立回滚。

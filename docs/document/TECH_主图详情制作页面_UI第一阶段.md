# 主图详情制作页面 UI 第一阶段技术设计

> 版本：v1.0
> 日期：2026-07-11
> 状态：技术方案已确认
> 任务等级：A级
> 前置文档：`UI_主图详情制作页面.md`
> 关联方案：`TECH_电商图片Agent_v3.md`

## 一、目标与实施边界

### 1.1 目标

在现有 React 前端中新增独立的 `/detail-page` 页面，通过 Mock 数据实现“输入 → 分析中 → 确认规划 → 生成中 → 完成”五步可点击流程，并从 Chat 左侧栏“AI 记忆”上方进入。

### 1.2 第一阶段包含

- 独立登录保护路由和页面懒加载。
- Chat 左侧栏固定入口。
- 顶部导航、五步进度条和响应式双栏布局。
- 产品图、参考图本地选择和预览。
- 主图/详情图、平台、要求、语言、比例、清晰度和数量设置。
- Step 2–5 Mock 状态和完整交互。
- UI 单元测试、Store 测试、构建和浏览器视觉验收。

### 1.3 第一阶段不包含

- 真实文件上传和 OSS 存储。
- 真实 AI 帮写、产品分析和图片生成。
- 真实积分扣除、退款和任务恢复。
- 后端接口、数据库和 WebSocket 变更。
- 广告图、历史项目、打包下载和二次精修。

第一阶段离开页面后不会继续执行 Mock 任务，也不会伪装后台恢复能力；真实异步行为在后续阶段接入服务端任务后实现。

## 二、项目上下文

### 2.1 架构现状

- 前端使用 React 19、TypeScript 5.9、Zustand 5、TailwindCSS 4、React Router 7、Vitest 和 Testing Library。
- `App.tsx` 统一管理懒加载、路由动画、认证初始化和登录保护路由。
- Chat 左侧栏由 `components/chat/layout/Sidebar.tsx` 管理，底部已有 AI 记忆和管理后台入口。
- 项目已有 Button、Card、Input、Modal、Radix Dropdown、Toast 和三层主题 Token，新增页面应复用现有设计系统。
- 当前图片生成失败、积分和刷新恢复链路已经完成代码改造但仍待生产验证，第一阶段不与其耦合。

### 2.2 可复用模块

| 模块 | 用途 |
|---|---|
| `ProtectedRoute` | 登录用户路由保护 |
| `Button` / `Card` / `Input` | 页面基础控件 |
| `Modal` | 删除确认、重新规划确认、图片预览 |
| `DropdownMenu` | 页面菜单类交互 |
| `LoadingScreen` | 路由首次加载 |
| `react-hot-toast` | 轻量成功提示 |
| `useAuthStore` | 当前用户、组织和积分信息 |
| Lucide React | 统一线性图标 |
| 页面过渡机制 | `/detail-page` 路由切换 |

### 2.3 设计约束

- 使用现有语义 Token，不硬编码流影 AI 的品牌颜色。
- TypeScript 禁止使用 `any`。
- 组件文件不超过 500 行，函数不超过 120 行。
- 所有定时器、ObjectURL 和事件监听器必须清理。
- 快速连续操作必须由状态锁防止重复执行。
- 第一阶段不调用真实服务，Mock 行为必须在代码结构和界面文案上明确可替换。
- 新模块放入 `components/detail-page/`，不继续膨胀 Chat 目录。

### 2.4 潜在冲突

- `useImageUpload` 会立即调用真实上传接口，不能直接用于第一阶段 Mock 选择器。
- `Sidebar.tsx` 当前 271 行，允许做小范围入口修改，但禁止在其中加入详情页业务状态。
- `App.tsx` 由路由段生成动画 Key，新路由必须补充 `getRouteKey` 测试。
- Mock 任务没有服务端持久化，离开页面后无法满足真实任务继续和刷新恢复；界面不能误导用户。

## 三、方案选择

### 3.1 对比

| 维度 | 单页面本地状态 | 模块化页面＋独立 Zustand Store |
|---|---|---|
| 初期文件数量 | 少 | 较多 |
| 文件长度 | 容易膨胀 | 可控制 |
| 单元测试 | 粗粒度 | 可分层测试 |
| 后续真实 API 接入 | 需要迁移状态 | 替换 Store Action |
| 刷新恢复扩展 | 需要重构 | 可扩展任务字段 |
| 风险 | 后续返工较大 | 初期结构略多 |

### 3.2 已确认方案

采用“模块化页面＋独立 Zustand Store”。页面组件只消费状态和触发 Action；Mock 时间线、规划和结果统一由 Store 管理，后续真实接口接入时保留组件契约。

## 四、前端状态设计

### 4.1 核心类型

```ts
type DetailPageStep = 1 | 2 | 3 | 4 | 5;
type ContentType = 'main_image' | 'detail_page';
type ImageCategory = 'product' | 'reference';
type ItemStatus = 'waiting' | 'generating' | 'completed' | 'failed';

interface LocalImageItem {
  id: string;
  category: ImageCategory;
  file: File;
  previewUrl: string;
  error: string | null;
}

interface GenerationForm {
  contentType: ContentType;
  platform: 'auto' | 'taobao' | 'tmall' | 'jd' | 'pdd';
  requirement: string;
  language: 'zh-CN' | 'none';
  aspectRatio: string;
  quality: '1k' | '2k' | '4k';
  count: number;
}

interface PlanItem {
  id: string;
  role: string;
  purpose: string;
  composition: string;
  title: string;
  subtitle: string;
  prompt: string;
  aspectRatio: string;
  hasText: boolean;
}

interface GenerationItem extends PlanItem {
  status: ItemStatus;
  previewUrl: string | null;
  error: string | null;
  refundedCredits: number;
  versions: string[];
}
```

### 4.2 Store 状态

| 字段 | 说明 |
|---|---|
| `step` | 当前步骤 1–5 |
| `images` | 产品图和参考图，共享 9 张上限 |
| `form` | Step 1 用户设置 |
| `analysisStage` | Step 2 当前 Mock 阶段 |
| `plan` | Step 3 规划列表 |
| `generationItems` | Step 4–5 单张状态和版本 |
| `isTransitioning` | 防止重复操作 |
| `formError` | 页面级输入错误 |
| `mockScenario` | 正常、积分不足、单张失败等演示场景 |

### 4.3 Store Action

| Action | 职责 |
|---|---|
| `addImages(category, files)` | 校验格式和共享 9 张上限，创建 ObjectURL |
| `removeImage(id)` | 删除图片并释放对应 ObjectURL |
| `updateForm(patch)` | 更新表单，切换类型时同步默认比例和字段文案 |
| `startAnalysis()` | 校验产品图并进入 Step 2 |
| `cancelAnalysis()` | 清理分析定时器，返回 Step 1并保留输入 |
| `completeAnalysis()` | 写入 Mock 规划并进入 Step 3 |
| `updatePlanItem(id, patch)` | 编辑标题、副标题和提示词 |
| `removePlanItem(id)` | 删除规划，至少保留 1 张 |
| `replan()` | 二次确认后重建 Mock 规划 |
| `startGeneration()` | 校验 Mock 积分场景，进入 Step 4 |
| `advanceGeneration()` | 逐张推进 Mock 状态 |
| `retryGeneration(id)` | 模拟失败图片重试，旧版本不覆盖 |
| `restart()` | 回到 Step 1，保留图片和设置，清空结果 |
| `reset()` | 清理定时器、ObjectURL 和全部状态 |

### 4.4 默认值

- `contentType`：`main_image`。
- `platform`：`auto`。
- `language`：`zh-CN`。
- 主图比例：`1:1`。
- 详情图比例：`3:4`。
- `quality`：`1k`。
- `count`：1，可选择 1–9。
- 模型选择不进入 Store，由系统默认模型概念占位。

## 五、组件设计

| 组件 | 主要 Props/依赖 | 职责 |
|---|---|---|
| `DetailPage` | Store | 页面组装、卸载清理、按 step 选择视图 |
| `DetailPageHeader` | user、credits、onBack | 顶部品牌、返回、积分和用户入口 |
| `StepBar` | step | 展示五步状态，不允许点击未来步骤 |
| `ProductImageSection` | images、add/remove | 产品图必填、参考图可选、共享上限 |
| `GenerationSettings` | form、updateForm | 类型、平台、要求、语言、比例、清晰度、数量 |
| `AnalyzingPanel` | stage、cancel | 阶段反馈和取消分析 |
| `PlanReviewPanel` | plan、actions | 规划列表和页面级操作 |
| `PlanCard` | item、update/remove | 单卡编辑、提示词折叠和删除 |
| `GenerationProgress` | items | 整体真实条目数进度和当前角色 |
| `GenerationCard` | item、retry | 单张等待、生成、成功和失败状态 |
| `ResultGallery` | items、actions | 完成结果、版本选择、下载演示和再次制作 |

父组件不向下传递整个 Store；子组件只接收必要字段和回调，降低耦合和无关重渲染。

## 六、文件结构与修改范围

### 6.1 新增文件

| 文件 | 预估行数 | 放置原因 |
|---|---:|---|
| `frontend/src/pages/DetailPage.tsx` | 100 | 路由页面容器 |
| `frontend/src/components/detail-page/DetailPageHeader.tsx` | 100 | 独立页面顶部 |
| `frontend/src/components/detail-page/StepBar.tsx` | 90 | 五步进度展示 |
| `frontend/src/components/detail-page/ProductImageSection.tsx` | 220 | 两类本地图片选择和预览 |
| `frontend/src/components/detail-page/GenerationSettings.tsx` | 240 | Step 1 表单设置 |
| `frontend/src/components/detail-page/AnalyzingPanel.tsx` | 100 | Step 2 |
| `frontend/src/components/detail-page/PlanReviewPanel.tsx` | 150 | Step 3 容器 |
| `frontend/src/components/detail-page/PlanCard.tsx` | 220 | 可编辑规划卡 |
| `frontend/src/components/detail-page/GenerationProgress.tsx` | 100 | Step 4 整体进度 |
| `frontend/src/components/detail-page/GenerationCard.tsx` | 180 | 单张生成状态 |
| `frontend/src/components/detail-page/ResultGallery.tsx` | 180 | Step 5 结果 |
| `frontend/src/stores/useDetailPageStore.ts` | 350 | 五步状态与 Mock Action |
| `frontend/src/types/detailPage.ts` | 130 | 页面专用类型 |
| `frontend/src/mocks/detailPageMocks.ts` | 180 | Mock 规划、结果和演示场景 |

测试文件与被测模块同目录的 `__tests__/` 放置，便于定位；每个测试文件控制在 500 行以内。

### 6.2 修改文件

| 文件 | 修改内容 |
|---|---|
| `frontend/src/App.tsx` | 懒加载页面并新增受保护路由 |
| `frontend/src/components/chat/layout/Sidebar.tsx` | 在 AI 记忆上方增加入口 |
| `frontend/src/__tests__/App.test.ts` | 增加 `/detail-page` Route Key 用例 |
| `docs/PROJECT_OVERVIEW.md` | 增加页面和模块目录说明 |
| `docs/FUNCTION_INDEX.md` | 登记新增 Store Action 和公共组件 |

### 6.3 不修改文件

- 后端全部文件。
- 数据库迁移。
- 现有 ImageAgent 和电商图片接口。
- `useImageUpload.ts`。
- Chat 消息、积分和 WebSocket Store。

## 七、边界与极限场景

| 场景 | 处理策略 | 模块 |
|---|---|---|
| 未登录访问 | `ProtectedRoute` 拦截 | App |
| 未上传产品图 | 禁用分析并显示原因 | Settings/Store |
| 只有参考图 | 仍禁止分析 | Store |
| 总数超过 9 张 | 整批拒绝超出选择并显示当前数量 | ImageSection |
| 非图片或格式错误 | 对应板块显示可修复错误 | ImageSection |
| 快速重复选择 | 函数式更新，以最新总数再次校验 | Store |
| 快速重复分析 | `isTransitioning` 锁定 | Store |
| 分析中取消 | 清理 Timer，保留输入 | Store |
| 页面卸载 | 清理全部 Timer 和 ObjectURL | Page/Store |
| 规划为空 | 使用固定一张兜底 Mock 规划 | Store |
| 删除最后一张 | 禁止并提示至少保留 1 张 | PlanReview |
| Mock 积分不足 | 停留 Step 3，不生成条目 | Store |
| 单张 Mock 失败 | 其他条目继续，失败条目标记退款 | Store |
| 重试单张 | 新版本追加，旧版本保留 | Store |
| 离开 Step 4 | 清理 Mock Timer，不宣称后台继续 | Page/Store |
| 手机复杂编辑 | 保留基础操作，提示桌面端体验更完整 | Responsive UI |

Mock 最多 9 个生成条目，不需要虚拟滚动或分页。

## 八、连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|---|---|---|
| 新增 `/detail-page` | App、App 测试 | 懒加载、受保护路由、Route Key 测试 |
| 新增 Sidebar 入口 | Sidebar、Sidebar 测试 | 入口顺序、文字、点击跳转 |
| 新增 `DetailPageStep` | Store、各步骤组件、测试 | 使用统一联合类型 |
| Store 表单字段 | Settings、Mock、Store 测试 | 默认值和切换行为一致 |
| 图片共享上限 | ImageSection、Store、测试 | 产品图＋参考图统一计数 |
| PlanItem 编辑 | PlanCard、Review、Mock | 标题/副标题/Prompt 同步 |
| GenerationItem 版本 | GenerationCard、Result、Store | 重试追加、不覆盖旧图 |
| 新增文件和公共函数 | 文档索引 | 更新 Overview 和 Function Index |

## 九、架构影响评估

| 维度 | 评估 | 风险 | 应对措施 |
|---|---|---|---|
| 模块边界 | 新模块独立于 Chat 业务 | 低 | 使用专用目录和 Store |
| 数据流 | Store 单向驱动组件 | 低 | Action 集中管理转换 |
| 扩展性 | 最多 9 个条目，无列表瓶颈 | 低 | 后续 API 不改变组件契约 |
| 耦合度 | 仅依赖认证、路由和 UI 组件 | 低 | 不接消息和任务 Store |
| 一致性 | 沿用 Zustand、Token、Radix、Vitest | 低 | 不新增平行设计系统 |
| 可观测性 | 第一阶段无真实业务请求 | 低 | 浏览器错误由现有 ErrorBoundary 捕获 |
| 可回滚性 | 无 DB/API，删除路由和入口即可 | 低 | 保持提交按模块拆分 |

无高风险架构问题。

## 十、测试与验证

### 10.1 单元测试

- Route Key：`/detail-page` 返回 `/detail-page`。
- Sidebar：入口位于 AI 记忆前、所有登录用户可见、点击跳转。
- Store：默认值、五步正向流转和重复操作锁。
- 图片：产品图必填、参考图可选、共享 9 张上限、格式错误。
- ObjectURL：新增时创建，删除和卸载时释放。
- 设置：主图/详情图切换默认比例，语言默认中文，数量 1–9。
- 分析：取消保留输入，完成进入 Step 3。
- 规划：可编辑、可删除但至少保留 1 张、积分不足不生成。
- 生成：逐张完成、单张失败不阻塞、失败按张退款演示。
- 完成：再次制作保留全部设置；单张重试不覆盖旧版本。

### 10.2 必跑命令

```bash
cd frontend
npm run test:run -- <相关测试文件>
npm run test:coverage -- <相关测试文件>
npm run build
```

代码完成后必须触发 `/everydayai-test-coverage`，扫描变更文件并补齐缺失测试。

### 10.3 浏览器验收

- 1280px、1440px、1920px 桌面宽度。
- 768–1279px 上下布局。
- 小于 768px 单列基础操作。
- 入口、五步正向流程、错误场景、键盘操作和无横向溢出。

## 十一、依赖、部署与回滚

### 11.1 依赖

无需新增 npm 依赖，全部复用现有精确锁定依赖。

### 11.2 数据库与 API

- 无数据库迁移。
- 无 API 变更。
- 不影响现有协议兼容性。

### 11.3 回滚

1. 移除 `App.tsx` 的 `/detail-page` 路由。
2. 移除 Sidebar 入口。
3. 删除 `pages/DetailPage.tsx`、`components/detail-page/`、专用 Store、类型、Mock 和测试。
4. 回退文档索引。

不需要数据库回滚、数据修复或后端部署。

## 十二、开发任务拆分

### Phase 1：基础骨架

- 新增类型、Mock 和 Zustand Store。
- 新增路由、页面容器、顶部导航和 StepBar。
- 新增 Sidebar 入口。
- 补路由和入口测试。

### Phase 2：Step 1

- 产品图/参考图本地选择器。
- 生成设置表单。
- 图片校验、ObjectURL 清理和响应式布局。
- 补上传和设置测试。

### Phase 3：Step 2–3

- 分析阶段 Mock 时间线。
- 规划卡片、编辑、删除和重新规划。
- Mock 积分不足场景。
- 补状态流转和规划测试。

### Phase 4：Step 4–5

- 逐张生成状态和单张失败。
- 完成页、再次制作和版本保留。
- 补生成、退款演示和完成页测试。

### Phase 5：闭环验证

- `/everydayai-test-coverage`。
- TypeScript、相关测试、覆盖率和生产构建。
- 多宽度浏览器视觉验收。
- 更新 `PROJECT_OVERVIEW.md` 和 `FUNCTION_INDEX.md`。
- 执行功能完成后的 `/everydayai-review`。

## 十三、风险

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| Mock 被误认为真实任务 | 中 | 明确阶段边界，不展示后台恢复承诺 |
| Store 超过 500 行 | 中 | 将纯 Mock 推进函数放入 mocks，Store 只编排状态 |
| ObjectURL 泄漏 | 中 | 删除和卸载双重释放，专项测试 |
| 大量组件导致过度拆分 | 低 | 只拆独立职责，不为单次文本创建组件 |
| 与后续 API 契约偏差 | 中 | 类型沿用 v3 content_type 和图片角色语义 |
| Sidebar 入口影响旧布局 | 低 | 外科式插入并补顺序测试 |

## 十四、文档更新清单

- `docs/PROJECT_OVERVIEW.md`：新增页面、组件目录、Store 和 Mock。
- `docs/FUNCTION_INDEX.md`：新增 Store Action 和公共组件索引。
- `docs/document/UI_主图详情制作页面.md`：已确认，无需改变 UI 需求。
- `docs/CURRENT_ISSUES.md`：第一阶段无阶段性风险遗留时不更新；若发现真实阻塞再记录。
- `docs/document/TECH_ARCHITECTURE.md`：不涉及系统架构变化，不更新。

## 十五、设计自检

- [x] 需求、方案评审和 UI 文档已确认。
- [x] 项目上下文包含架构现状、可复用模块、设计约束和潜在冲突。
- [x] 边界、连锁影响和架构影响已评估。
- [x] 无高风险架构问题。
- [x] 无数据库、API 和依赖变更。
- [x] 所有新增文件预估不超过 500 行。
- [x] 函数设计目标不超过 120 行。
- [x] 测试、文档和回滚路径完整。

---

**用户确认本技术设计后，进入第一阶段 UI 开发。**

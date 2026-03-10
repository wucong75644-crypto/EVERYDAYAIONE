# UI 设计文档：模型广场首页改版

> **版本**: v1.0 | **日期**: 2026-03-09
> **需求文档**: `REQ_模型广场首页改版.md`

---

## 0. 现有 UI 分析

### 可复用组件

| 组件 | 路径 | 复用方式 |
|------|------|---------|
| Modal | `components/common/Modal.tsx` | 参考其动画/遮罩逻辑，Drawer 需新建 |
| Footer | `components/Footer.tsx` | 直接复用（standard 模式） |
| LoadingScreen | `components/common/LoadingScreen.tsx` | 首页初始加载 |
| ErrorBoundary | `components/common/ErrorBoundary.tsx` | 已全局包裹 |
| AuthModal | `components/auth/AuthModal.tsx` | 登录/注册弹窗，直接复用 |
| react-hot-toast | 全局 | 订阅成功/失败提示 |

### 样式约束

| 规范 | 值 |
|------|---|
| 主色 | `blue-600`（按钮/链接/激活态） |
| 文本色 | `gray-900`（主）/ `gray-600`（次）/ `gray-500`（弱） |
| 背景色 | `gray-50`（页面）/ `white`（卡片/面板） |
| 边框色 | `gray-200`（默认）/ `gray-300`（输入框） |
| 圆角 | `rounded-lg`（按钮/输入框）/ `rounded-xl`（卡片/弹窗） |
| 阴影 | `shadow-sm`（悬浮）/ `shadow-lg`（弹窗）/ `shadow-2xl`（重要面板） |
| 动画时长 | 150-200ms（UI交互）/ 250ms（重要过渡） |
| 按钮主色 | `bg-blue-600 text-white hover:bg-blue-700 transition-colors` |
| 按钮次色 | `border border-gray-300 text-gray-700 hover:bg-gray-50` |
| 禁用态 | `opacity-50 cursor-not-allowed` |
| z-index | 模态层 `z-50`，下拉 `z-10` |

### 布局模式
- 页面容器：`max-w-7xl mx-auto px-4 sm:px-6 lg:px-8`
- 全屏布局：`min-h-screen bg-gray-50`
- Tab 激活态：`text-blue-600 border-b-2 border-blue-600`

### 交互惯例
- 操作中：按钮 disabled + spinner
- 失败：`react-hot-toast` 红色提示
- 空态：居中图标 + 说明文字 + 引导按钮
- 弹窗：ESC / 点击遮罩关闭

---

## 1. 页面结构

### 页面：首页（模型广场）
- **路由**：`/`
- **权限**：公开（未登录可浏览，已登录可操作）
- **布局**：

```
┌──────────────────────────────────────────────────────────────┐
│  NavBar（固定顶部）                                           │
│  [Logo]                              [积分|头像] 或 [登录]    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  HeroSection                                                 │
│  标题 + 副标题 + 搜索框                                       │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  CategoryTabs                                                │
│  [全部] [💬聊天] [🎨图片] [🎬视频]                            │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ModelGrid                                                   │
│                                                              │
│  (Tab=全部 时，按类别分组显示，每组有标题)                      │
│                                                              │
│  ── 💬 聊天模型 (20) ──                                      │
│  [Card] [Card] [Card] [Card]                                 │
│  [Card] [Card] [Card] [Card]                                 │
│  ...                                                         │
│                                                              │
│  ── 🎨 图片模型 (3) ──                                       │
│  [Card] [Card] [Card]                                        │
│                                                              │
│  ── 🎬 视频模型 (3) ──                                       │
│  [Card] [Card] [Card]                                        │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  Footer（备案信息）                                           │
└──────────────────────────────────────────────────────────────┘
```

### 叠加层：模型详情 Drawer
- **触发**：点击模型卡片
- **展现**：右侧滑入 Drawer（400px），左侧遮罩变暗

```
┌──────────── 首页（遮罩变暗）───────┬──── DetailDrawer (400px) ──┐
│                                   │                            │
│                                   │  模型名称           [×关闭] │
│                                   │  ─────────────────         │
│                                   │  描述                      │
│                                   │  能力标签                   │
│                                   │  规格参数                   │
│                                   │  费用信息                   │
│                                   │  ─────────────────         │
│                                   │  [操作按钮区]               │
│                                   │                            │
└───────────────────────────────────┴────────────────────────────┘
```

---

## 2. 组件详细设计

### 2.1 NavBar（导航栏）

```
┌──────────────────────────────────────────────────────────────┐
│  EVERYDAYAI                              [1,250积分] [头像▼]  │
└──────────────────────────────────────────────────────────────┘

未登录时：
┌──────────────────────────────────────────────────────────────┐
│  EVERYDAYAI                                [登录] [免费注册]  │
└──────────────────────────────────────────────────────────────┘
```

- 固定顶部，白色背景 + `shadow-sm`
- Logo：`text-xl font-bold text-gray-900`
- 已登录：积分数 (`text-gray-600 text-sm`) + 头像/昵称
- 未登录：
  - [登录]：`text-gray-600 hover:text-gray-900`（文字按钮）
  - [免费注册]：`bg-blue-600 text-white rounded-lg px-4 py-2`（主按钮）
- 点击 [登录] / [免费注册]：触发 AuthModal（复用现有）

### 2.2 HeroSection（品牌区）

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│          EVERYDAYAI — 你的全能 AI 创作平台                    │
│     44+ 顶尖模型，聊天 · 绘图 · 视频一站搞定                 │
│                                                              │
│         [🔍 搜索模型名称或描述...]                            │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

- 居中排版，`text-center`
- 标题：`text-3xl sm:text-4xl font-bold text-gray-900`
- 副标题：`text-lg text-gray-500 mt-2`
- 搜索框：`max-w-md mx-auto mt-6`
  - 样式：`px-4 py-2.5 rounded-xl border border-gray-300 shadow-sm w-full`
  - 左侧搜索图标（lucide `Search`）
  - placeholder：`搜索模型名称或描述...`
  - 输入即搜索（debounce 300ms）
- 整体 padding：`py-10 sm:py-14`

### 2.3 CategoryTabs（分类标签）

```
  [全部]   [💬 聊天]   [🎨 图片]   [🎬 视频]
  ═══════
  (蓝色下划线表示当前选中)
```

- 容器：`border-b border-gray-200`，居中或左对齐
- Tab 项：`px-4 py-2.5 text-sm font-medium cursor-pointer transition-colors`
- 激活态：`text-blue-600 border-b-2 border-blue-600`（与现有 LoginForm Tab 一致）
- 未激活：`text-gray-500 hover:text-gray-700`
- 每个 Tab 显示计数：`聊天 (20)`
- 粘性定位：`sticky top-[64px] bg-white z-10`（64px = NavBar 高度，滚动时固定）

### 2.4 ModelCard（模型卡片）

```
┌─────────────────────────┐
│ [🟢 免费]               │  ← 左上角标签
│                         │
│  Gemini 3 Flash         │  ← 模型名称
│  快速响应 | 多模态理解   │  ← 一句话描述
│                         │
│  [📝] [🖼️] [🎤] [📄] [🔧]│  ← 能力图标行
│                         │
│  免费                   │  ← 费用（或 "X积分/次"）
│                         │
│  ━━━━━━━━━━━━━━━━━━━━━  │  ← 分割线
│       [✓ 已订阅]        │  ← 按钮区
└─────────────────────────┘
```

**卡片容器样式**：
- 默认：`bg-white rounded-xl border border-gray-200 hover:shadow-md transition-shadow cursor-pointer`
- 已订阅：`border-blue-200 bg-blue-50/30`（轻微蓝色调）
- hover：`hover:shadow-md hover:-translate-y-0.5 transition-all duration-200`

**标签（左上角）**：
- 免费：`bg-green-100 text-green-700 text-xs px-2 py-0.5 rounded-full font-medium`
- 维护中：`bg-orange-100 text-orange-700 text-xs px-2 py-0.5 rounded-full`
- 无标签时此区域不占位

**模型名称**：`text-base font-semibold text-gray-900 mt-3`

**描述**：`text-sm text-gray-500 mt-1 line-clamp-1`（单行截断）

**能力图标行**：
- 容器：`flex flex-wrap gap-1.5 mt-3`
- 每个图标：`text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600`
- 图标 + 文字：如 `📝文本` `🖼️图片` `🎤音频`
- 超过 4 个时显示 `+N`

**费用**：
- 免费：`text-sm font-medium text-green-600 mt-2`，显示"免费"
- 收费：`text-sm text-gray-600 mt-2`，显示"X积分/次"
- 多价格（图片/视频）：显示起步价"18积分起"

**分割线**：`border-t border-gray-100 mt-3`

**按钮区**：
- 容器：`px-4 py-3 text-center`
- 未订阅 [订阅]：`text-sm font-medium text-blue-600 hover:text-blue-700`
- 已订阅 [✓ 已订阅]：`text-sm text-gray-400`
- 默认模型 [✓ 默认]：`text-sm text-gray-400 cursor-default`
- 维护中 [维护中]：`text-sm text-orange-500 cursor-default`
- 未登录：不显示按钮区，分割线也隐藏

**卡片内部 padding**：`p-4`（上半部分），按钮区单独 padding

**点击行为**：
- 点击卡片（非按钮）→ 打开 DetailDrawer
- 点击订阅按钮 → 调用订阅 API（`e.stopPropagation()` 阻止冒泡）

### 2.5 ModelGrid（卡片网格）

**"全部" Tab 下**：
```
── 💬 聊天模型 (20) ────────────────────────
[Card] [Card] [Card] [Card]
[Card] [Card] ...

── 🎨 图片模型 (3) ─────────────────────────
[Card] [Card] [Card]

── 🎬 视频模型 (3) ─────────────────────────
[Card] [Card] [Card]
```

- 分组标题：`text-lg font-semibold text-gray-800 mb-4 mt-8`
  - 首组 `mt-0`
  - 格式：`💬 聊天模型 (20)` — 图标 + 类别名 + 数量
- 网格：`grid gap-4`
  - xl (≥1280): `grid-cols-4`
  - lg (≥1024): `grid-cols-3`
  - md (≥768): `grid-cols-2`
  - sm (<768): `grid-cols-1`（或 `grid-cols-2` 紧凑版）

**单类别 Tab 下**：
- 无分组标题，直接铺卡片网格
- 顶部显示类别描述（可选）

**搜索结果**：
- 有结果：平铺网格，不分组
- 无结果：居中显示"未找到匹配的模型"
  - 图标：`Search` (lucide) `text-gray-300 w-12 h-12`
  - 文字：`text-gray-500 mt-4`
  - 建议："试试其他关键词"

**智能模型 (auto) 不展示在卡片网格中**（它是路由层概念，不是独立模型）

### 2.6 ModelDetailDrawer（模型详情抽屉）

**整体结构**：

```
┌──────────────────────────────────────┐
│                                      │
│  GPT-5.4                      [×]    │  ← 顶部：名称 + 关闭按钮
│                                      │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   │
│                                      │
│  OpenAI 最新旗舰 | 百万上下文 | 全能  │  ← 描述
│                                      │
│  ── 模型能力                         │
│  [📝 文本对话] [🖼️ 图片理解]         │  ← 能力标签（完整版）
│  [🔧 工具调用] [📊 JSON输出]         │
│  [⚡ 流式响应]                       │
│                                      │
│  ── 规格参数                         │
│  上下文长度    1,050,000 tokens      │  ← 参数列表
│  最大图片数    10 张                  │
│  图片限制      ≤ 20MB               │
│                                      │
│  ── 费用                             │
│  单次消耗      免费 / 5积分           │
│                                      │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   │
│                                      │
│  [↗ 前往聊天页]  ← 已订阅时          │  ← 底部操作区
│  或                                  │
│  [请先订阅] [订阅]  ← 未订阅时       │
│  或                                  │
│  [登录后即可使用] [立即注册]          │  ← 未登录时
│                                      │
│  [取消订阅]  ← 仅已订阅+非默认       │
│                                      │
└──────────────────────────────────────┘
```

**Drawer 容器**：
- 遮罩：`fixed inset-0 bg-black/40 z-50`
- 面板：`fixed right-0 top-0 h-full w-[400px] bg-white shadow-2xl z-50`
- 移动端：`w-full`（全屏）
- 动画：从右侧滑入 `translateX(100%) → translateX(0)`，200ms ease-out
- 关闭动画：`translateX(0) → translateX(100%)`，150ms

**头部**：
- 模型名称：`text-xl font-bold text-gray-900`
- 关闭按钮：`p-2 hover:bg-gray-100 rounded-lg`（lucide `X` 图标）
- padding：`px-6 py-5`
- 底部分割线：`border-b border-gray-200`

**内容区**（可滚动）：
- `flex-1 overflow-y-auto px-6 py-5`
- 描述：`text-sm text-gray-600`
- 小节标题：`text-sm font-semibold text-gray-800 mt-6 mb-3`
- 能力标签：`inline-flex items-center px-2.5 py-1 rounded-lg bg-blue-50 text-blue-700 text-sm mr-2 mb-2`
- 参数列表：两列布局
  - 左列（label）：`text-sm text-gray-500`
  - 右列（value）：`text-sm font-medium text-gray-900`
  - 行间距：`space-y-2.5`

**费用展示**：

聊天模型（单价）：
```
单次消耗    免费          ← text-green-600 font-medium
单次消耗    5 积分/次     ← text-gray-900 font-medium
```

图片模型（分辨率价格表）：
```
┌──────────┬────────┐
│ 分辨率   │ 费用    │
├──────────┼────────┤
│ 1K       │ 18积分  │
│ 2K       │ 18积分  │
│ 4K       │ 24积分  │
└──────────┴────────┘
```
- 表格样式：`text-sm` + `border border-gray-100 rounded-lg overflow-hidden`

视频模型（时长价格表）：
```
┌──────────┬────────┐
│ 时长     │ 费用    │
├──────────┼────────┤
│ 10秒     │ 30积分  │
│ 15秒     │ 45积分  │
│ 25秒     │ 270积分 │
└──────────┴────────┘
```

**底部操作区**：
- 容器：`px-6 py-4 border-t border-gray-200 bg-white`（固定底部）
- 按钮铺满宽度

**状态A：已登录 + 已订阅**
```
[↗ 前往聊天页]                    ← 主按钮（蓝色实心，宽度100%）
[取消订阅]                        ← 次要链接（红色文字，居中，仅非默认模型显示）
```
- 主按钮：`w-full bg-blue-600 text-white py-2.5 rounded-lg font-medium hover:bg-blue-700`
- 取消订阅：`text-sm text-red-500 hover:text-red-600 mt-3 text-center cursor-pointer`

**状态B：已登录 + 未订阅**
```
  请先订阅才能使用该模型           ← 提示文字
[订阅此模型]                      ← 主按钮（蓝色实心）
```
- 提示：`text-sm text-gray-500 text-center mb-3`
- 按钮：同上主按钮样式

**状态C：未登录**
```
  登录后即可使用，注册送100积分    ← 提示文字
[立即注册]                        ← 主按钮（蓝色实心）
[已有账号？登录]                  ← 次要链接
```
- 提示：`text-sm text-gray-500 text-center mb-3`
- 主按钮：同上
- 次要链接：`text-sm text-blue-600 hover:text-blue-700 mt-2 text-center`

**状态D：默认模型（已订阅 + 不可取消）**
```
[↗ 前往聊天页]                    ← 主按钮
  系统默认模型，无法取消订阅       ← 灰色提示文字（无取消按钮）
```

---

## 3. 交互流程

### 流程1：浏览模型（所有用户）
1. 用户访问首页 `/`
2. 页面加载：NavBar + HeroSection + CategoryTabs + ModelGrid
3. 默认 Tab "全部"，显示所有模型按分组排列
4. 用户可切换 Tab 过滤类别
5. 用户可在搜索框输入，实时过滤模型（debounce 300ms）
6. 滚动浏览所有模型卡片

### 流程2：查看模型详情（所有用户）
1. 用户点击某个模型卡片
2. 右侧滑入 DetailDrawer，背景变暗
3. 展示模型完整信息（能力、参数、费用）
4. 底部根据登录/订阅状态显示不同操作
5. 关闭：点击 × / 点击遮罩 / ESC

### 流程3：订阅模型（已登录用户）

**方式A：卡片直接订阅**
1. 用户在卡片底部点击 [订阅]
2. 按钮变为 loading（spinner + 文字"订阅中..."）
3. 调用订阅 API
4. 成功：按钮变为 [✓ 已订阅]，卡片边框变蓝色调，Toast "订阅成功"
5. 失败：Toast 错误提示，按钮恢复 [订阅]

**方式B：详情面板订阅**
1. 用户在 DetailDrawer 底部点击 [订阅此模型]
2. 按钮 loading
3. 成功：底部区域变为 [↗ 前往聊天页] + [取消订阅]
4. 失败：Toast 错误提示

### 流程4：取消订阅（已登录用户）
1. 用户在 DetailDrawer 底部点击 [取消订阅]
2. 弹出确认弹窗（复用 Modal 组件）：
   ```
   确认取消订阅？
   取消后将无法在聊天页使用该模型
   [确认取消]  [保持订阅]
   ```
3. 确认 → 调用取消 API → 成功：底部变为 [订阅此模型]，Toast "已取消订阅"
4. 保持 → 关闭弹窗

### 流程5：跳转聊天（已登录 + 已订阅）
1. 用户在 DetailDrawer 点击 [↗ 前往聊天页]
2. 跳转到 `/chat?model={modelId}`
3. 聊天页自动选中该模型

### 流程6：未登录用户引导
1. 用户在 DetailDrawer 看到 [立即注册]
2. 点击 → 关闭 Drawer → 打开 AuthModal（注册模式）
3. 注册/登录成功 → 自动订阅默认模型 → 页面刷新显示订阅状态

---

## 4. 状态设计

### 4.1 首页整体状态

| 状态 | 触发条件 | 显示内容 | 可操作性 |
|------|---------|---------|---------|
| 加载中 | 页面首次加载/获取订阅列表 | 卡片区域显示骨架屏（8个卡片占位） | Tab/搜索可用 |
| 正常 | 数据加载完成 | 模型卡片网格 | 全部可操作 |
| 搜索无结果 | 搜索输入无匹配 | 居中空态：搜索图标 + "未找到匹配的模型" | 可修改搜索词 |
| 网络错误 | API 请求失败 | Toast 错误提示，卡片显示前端已有的模型数据 | 可重试 |

### 4.2 模型卡片状态

| 状态 | 视觉表现 | 按钮 |
|------|---------|------|
| 未登录 | 默认样式，无按钮区 | 无 |
| 已登录 + 未订阅 | 默认样式 | [订阅]（蓝色文字） |
| 已登录 + 已订阅 | `border-blue-200 bg-blue-50/30` | [✓ 已订阅]（灰色） |
| 已登录 + 默认模型 | 同已订阅 | [✓ 默认]（灰色禁用） |
| 订阅中 | 同默认 | spinner + "订阅中..."（禁用） |
| 维护中 | `opacity-60` | [维护中]（橙色禁用） |

### 4.3 DetailDrawer 底部操作区状态

| 用户状态 | 订阅状态 | 显示内容 |
|---------|---------|---------|
| 未登录 | — | 提示文字 + [立即注册] + [已有账号？登录] |
| 已登录 | 未订阅 | "请先订阅" + [订阅此模型] |
| 已登录 | 订阅中 | [订阅中...] spinner 按钮禁用 |
| 已登录 | 已订阅 | [↗ 前往聊天页] + [取消订阅] |
| 已登录 | 默认模型 | [↗ 前往聊天页] + 灰色提示"系统默认" |
| 已登录 | 取消中 | [取消中...] spinner |

### 4.4 骨架屏设计

```
┌─────────────────────────┐
│ [▓▓▓▓]                  │  ← 标签占位
│                         │
│  ▓▓▓▓▓▓▓▓▓▓            │  ← 名称占位
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓       │  ← 描述占位
│                         │
│  [▓] [▓] [▓]           │  ← 能力图标占位
│                         │
│  ▓▓▓▓▓                 │  ← 费用占位
│  ━━━━━━━━━━━━━━━━━━━━━  │
│  ▓▓▓▓▓▓▓▓              │  ← 按钮占位
└─────────────────────────┘
```
- 使用 `animate-pulse bg-gray-200 rounded`
- 显示 8 个骨架卡片（2行 × 4列）

---

## 5. 组件清单

| 组件名 | 功能 | 复用/新建 | 路径 |
|-------|------|----------|------|
| NavBar | 首页顶部导航（Logo+用户信息/登录按钮） | 新建 | `components/home/NavBar.tsx` |
| HeroSection | 品牌标题+搜索框 | 新建 | `components/home/HeroSection.tsx` |
| CategoryTabs | 分类标签切换（全部/聊天/图片/视频） | 新建 | `components/home/CategoryTabs.tsx` |
| ModelGrid | 模型卡片网格布局 + 分组标题 + 搜索过滤 | 新建 | `components/home/ModelGrid.tsx` |
| ModelCard | 单个模型卡片（信息+订阅按钮） | 新建 | `components/home/ModelCard.tsx` |
| ModelCardSkeleton | 模型卡片骨架屏 | 新建 | `components/home/ModelCardSkeleton.tsx` |
| ModelDetailDrawer | 右侧详情抽屉面板 | 新建 | `components/home/ModelDetailDrawer.tsx` |
| UnsubscribeConfirmModal | 取消订阅确认弹窗 | 新建（基于 Modal） | `components/home/UnsubscribeConfirmModal.tsx` |
| Footer | 备案信息 | 复用 | `components/Footer.tsx` |
| AuthModal | 登录/注册弹窗 | 复用 | `components/auth/AuthModal.tsx` |
| Modal | 通用弹窗 | 复用 | `components/common/Modal.tsx` |
| react-hot-toast | 操作反馈提示 | 复用 | 全局 |

---

## 6. 响应式设计

### 断点行为

| 区域 | ≥1280px (xl) | ≥1024px (lg) | ≥768px (md) | <768px (sm) |
|------|-------------|-------------|-------------|-------------|
| NavBar | 完整展示 | 完整展示 | 完整展示 | Logo + 汉堡菜单（可选） |
| Hero 标题 | `text-4xl` | `text-3xl` | `text-3xl` | `text-2xl` |
| 搜索框 | `max-w-md` | `max-w-md` | `max-w-sm` | `w-full px-4` |
| CategoryTabs | 水平排列 | 水平排列 | 水平排列 | 水平滚动 |
| ModelGrid | 4列 | 3列 | 2列 | 1列 |
| DetailDrawer | 右侧 400px | 右侧 400px | 右侧 360px | 全屏 |

### 移动端特殊处理
- DetailDrawer 在 `<768px` 时全屏展示（`w-full`），从底部滑上来
- CategoryTabs 在移动端可水平滚动（`overflow-x-auto`）
- 卡片在 1 列模式下宽度更大，能力图标完整展示

---

## 7. 动画规范

| 交互 | 动画 | 时长 | 缓动 |
|------|------|------|------|
| Drawer 打开 | `translateX(100%) → 0` | 200ms | ease-out |
| Drawer 关闭 | `translateX(0) → 100%` | 150ms | ease-in |
| 遮罩打开 | `opacity: 0 → 1` | 200ms | ease |
| 遮罩关闭 | `opacity: 1 → 0` | 150ms | ease |
| 卡片 hover | `translateY(0) → -2px` + `shadow-md` | 200ms | ease |
| 订阅按钮状态变化 | 淡入淡出 | 150ms | ease |
| 搜索过滤 | 卡片淡入 | 150ms | ease |
| Tab 切换 | 下划线滑动 | 150ms | ease |

需要在 `index.css` 中新增的动画：
```
drawerSlideIn:  translateX(100%) → translateX(0)
drawerSlideOut: translateX(0) → translateX(100%)
```

---

**确认后进入技术设计（`@3-dev-doc`）**

# 技术设计：前端设计系统重构（多主题 + 组件库统一 + 苹果级丝滑动画）

> 版本：V1.5 | 日期：2026-04-10
> V1.1 变更：多主题系统（经典蓝保留为默认，Claude 暖色为可选）
> V1.2 变更：PoC 验证 Tailwind v4 @theme 运行时覆盖可行，简化方案；补 thinking 动画去重
> V1.3 变更：全面体检补充 8 项遗漏（ErrorBoundary/Modal 统一/菜单统一/cn()/SVG 清理/z-index/focus-visible/目录拆分）
> V1.4 变更：二轮查漏 — 测试断言同步更新、Toaster 主题适配、useMessageAnimation class 名迁移
> V1.5 变更：三轮查漏 — @variant dark 配置、Toaster 未挂载 bug 修复、渐变 CSS 变量方案、::selection/滚动条主题化、主题切换过渡动画

## 1. 现有代码分析

### 已阅读文件

| 文件 | 关键理解 |
|------|---------|
| `index.css` | 全局样式入口，Tailwind v4 `@import "tailwindcss"` + 19 个 @keyframes 动画（300行） |
| `markdown.css` | Markdown 渲染样式，硬编码 8 个十六进制颜色值 |
| `shared.module.css` | CSS Module，bounce dot 动画 + 动态 CSS 变量定位/尺寸 |
| `animations.ts` | 6 个动画时长常量（JS 侧 setTimeout 用） |
| `useModalAnimation.ts` | Modal 开关状态机 Hook（isOpen/isClosing + 定时器清理） |
| `useMessageAnimation.ts` | 消息进入/删除动画状态机（新消息检测 + 角色区分） |
| `Modal.tsx` | 通用模态框，`bg-white rounded-xl shadow-2xl` + transition 动画 |
| `Chat.tsx` | 聊天页根组件，`bg-gray-50` 背景 + flex 三栏布局 |
| `Home.tsx` | 首页，`bg-gray-50` 背景 |
| `MessageItem.tsx` | 消息组件，用户气泡 `bg-gradient-to-r from-purple-500 to-indigo-500` |
| 10 个核心组件 | ChatHeader/Sidebar/InputArea/ModelCard/NavBar/LoginForm/SettingsModal/ConversationItem/ModelSelector/MessageItem |

### 可复用模块

- **动画 Hook**：`useModalAnimation` + `useMessageAnimation` 模式成熟，可直接复用
- **动画常量**：`animations.ts` 的时长同步机制保留
- **Modal 组件**：结构好，只需换色+换动画曲线
- **CSS Module 方案**：`shared.module.css` 的 CSS 变量动态定位方案保留

### 设计约束

- Tailwind CSS v4.1 使用 `@theme` 指令定义 token（不用 JS 配置文件）
- `@theme` 中定义的变量自动生成 utility class（如 `--color-primary` → `bg-primary`）
- 现有 47 个 `.tsx` 文件包含硬编码颜色，逐步迁移
- 动画 keyframes 定义在 CSS 中，JS 侧只管时长和状态

### 连锁修改清单

| 改动点 | 影响范围 | 必须同步修改 |
|--------|---------|------------|
| `bg-gray-50` 页面背景 → `bg-surface` | Chat.tsx、Home.tsx、ForgotPassword.tsx | 3 个页面根元素 |
| `bg-white` 容器背景 → `bg-surface-card` | 47 个组件 | 逐 Phase 迁移，不一次性改 |
| `bg-blue-600` 主按钮 → `bg-accent` | ~30 处 | 所有主按钮（classic 下仍显示蓝色） |
| `border-gray-200` → `border-border-default` | 31 个文件 71 处 | 逐 Phase 迁移 |
| `text-gray-900/700/500/400` → 语义 token | 全部组件 | 逐 Phase 迁移 |
| `transition-colors` → 统一 transition token | 46 个文件 158 处 | Phase 3 迁移 |
| `markdown.css` 硬编码色值 | 8 处 | 改为 CSS 变量引用 |
| `from-purple-500 to-indigo-500` 用户气泡 | MessageItem.tsx | 改为 `var(--color-user-bubble-from/to)`，classic 仍为紫色 |
| `rounded-lg/xl/2xl` 混用 | 全部组件 | token 化后统一 |
| `shadow-sm/md/lg` | 全部组件 | 改为 token 变量（不同主题不同阴影色温） |

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| token 定义后旧 class 未迁移 | 共存策略：@theme 只新增 token，不覆盖 Tailwind 默认值，旧 class 照常工作 | index.css |
| Dark 模式切换闪白屏 | `<html>` 上用 `color-scheme: dark`，初始化时从 localStorage 读主题写入 html class（在 React hydrate 之前） | main.tsx / index.html |
| Tailwind v4 `dark:` 前缀默认基于媒体查询 | 在 theme.css 中加 `@variant dark (&:where(.dark, .dark *));` 改为 class 策略，已有 26 处 `dark:` 自动生效 | theme.css |
| `<Toaster />` 未挂载（既有 bug） | App.tsx 加 `<Toaster />` 组件 + 传入 CSS 变量样式适配主题 | App.tsx |
| CSS 渐变不能直接用 token（`from-purple-500`） | 改为 `from-[var(--color-user-bubble-from)]` 或自定义 utility class | MessageItem.tsx, ForgotPassword.tsx |
| `/50` opacity 修饰符与 CSS 变量 | Tailwind v4 支持 `bg-surface/50` 语法，无需特殊处理 | 全局 |
| 动画偏好：用户开启"减少动画" | `@media (prefers-reduced-motion: reduce)` 下禁用所有非必要动画 | index.css |
| 移动端性能 | `will-change` 仅在动画播放时添加，动画结束移除；避免 box-shadow 动画（改用 opacity） | 动画系统 |
| 字体加载闪烁（FOIT/FOUT） | 系统字体栈优先，不加载 web font，零 FOIT 风险 | @theme font-family |
| 主题切换时组件闪烁 | CSS 变量切换是同步的（一帧内完成），不会出现中间态 | CSS 变量 |
| 渐进式迁移中新旧样式混合 | token class 和 Tailwind 默认 class 可以共存，不冲突 | 全局 |
| markdown.css 硬编码色值 | 改为 `var(--color-xxx)` 引用，dark 模式自动适配 | markdown.css |
| 第三方库样式（highlight.js/KaTeX/mermaid） | 保持独立，不纳入 token 系统 | 第三方 CSS |

## 3. 技术栈

- 前端：React 19 + TypeScript 5.9 + Zustand 5
- 构建：Vite 7.2 + @tailwindcss/vite 4.1
- 样式：Tailwind CSS 4.1（`@theme` 指令定义 token）
- 图标：Lucide React 0.563
- 动画：CSS @keyframes + `useModalAnimation` / `useMessageAnimation` Hook
- 无新增依赖

## 4. 目录结构

### 新增文件

| 文件 | 职责 | 行数估计 |
|------|------|---------|
| `frontend/src/styles/theme.css` | Design Token 定义（@theme 共享 token + classic/claude 两套主题变量 + dark 预留 + z-index 体系） | ~300 |
| `frontend/src/styles/animations.css` | 动画系统（从 index.css 拆出，用 token 变量） | ~250 |
| `frontend/src/components/ui/Button.tsx` | 统一按钮组件（5 variant × 3 size + loading + icon + active/focus-visible 状态） | ~130 |
| `frontend/src/components/ui/Input.tsx` | 统一输入框组件（label + error + icon + focus-visible） | ~80 |
| `frontend/src/components/ui/Card.tsx` | 统一卡片容器 | ~50 |
| `frontend/src/components/ui/Badge.tsx` | 统一标签/徽章 | ~60 |
| `frontend/src/components/ui/Dropdown.tsx` | 统一下拉菜单（内置动画 + 定位） | ~150 |
| `frontend/src/hooks/useTheme.ts` | 主题切换 Hook（theme 风格 + colorMode 明暗 + localStorage） | ~60 |
| `frontend/src/utils/cn.ts` | className 条件拼接工具函数（替代 148+ 处模板字符串拼接） | ~15 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `frontend/src/index.css` | 精简为 3 行 import（tailwindcss + theme.css + animations.css） |
| `frontend/src/styles/markdown.css`（从 components/chat/ 迁移） | 硬编码色值 → CSS 变量 |
| `frontend/src/constants/animations.ts` | 时长常量改为读取 CSS 变量值 |
| `frontend/src/components/common/Modal.tsx` | 换色 + 换动画曲线 + token 化 |
| `frontend/src/components/common/ErrorBoundary.tsx` | 11 处内联样式 → Tailwind token class（去掉 zIndex:99999） |
| `frontend/src/pages/Chat.tsx` | `bg-gray-50` → `bg-surface` |
| `frontend/src/pages/Home.tsx` | `bg-gray-50` → `bg-surface` |
| `frontend/src/components/chat/MessageItem.tsx` | 用户气泡渐变 → CSS 变量（随主题变化） |
| `frontend/src/components/chat/DeleteConfirmModal.tsx` | 统一改用 common/Modal |
| `frontend/src/components/chat/DeleteMessageModal.tsx` | 统一改用 common/Modal |
| `frontend/src/components/chat/TableExportModal.tsx` | 统一改用 common/Modal |
| `frontend/src/components/chat/ImagePreviewModal.tsx` | 内联 keyframe → animations.css |
| `frontend/src/components/chat/FilePreviewModal.tsx` | 内联 keyframe → animations.css |
| `frontend/src/components/home/ModelDetailDrawer.tsx` | 复用 useModalAnimation Hook |
| 15+ 处内联 SVG（Modal/DeleteConfirmModal/MessageActions 等） | 替换为 lucide-react 图标 |
| 其余 ~40 个组件文件 | Phase 3 逐步迁移 class name |

### chat/ 目录拆分（Phase 5）

当前 38 个文件平铺，按功能拆分为子目录：

| 子目录 | 文件 | 数量 |
|--------|------|------|
| `chat/modals/` | DeleteConfirmModal, DeleteMessageModal, FilePreviewModal, ImagePreviewModal, MemoryModal, SettingsModal, TableExportModal | 7 |
| `chat/menus/` | AdvancedSettingsMenu, ContextMenu, DropdownMenu, ImageContextMenu, UploadMenu | 5 |
| `chat/media/` | AudioPreview, AudioRecorder, AiImageGrid, FileCard, FilePreview, ImagePreview, MediaPlaceholder, MessageMedia | 8 |
| `chat/message/` | MessageItem, MessageActions, CodeBlock, MarkdownRenderer, ThinkingBlock, LoadingPlaceholder | 6 |
| `chat/` (保留) | ChatHeader, Sidebar, ConversationList, ConversationItem, InputArea, InputControls, ModelSelector, MessageArea, EmptyState, LoadingSkeleton, ConflictAlert, UploadErrorBar | 12 |

## 5. 数据库设计

无数据库变更。

## 6. API 设计

无 API 变更。纯前端重构。

## 7. 核心设计：多主题 Design Token 体系

### 7.0 多主题架构

**核心思路**：所有组件只引用语义化 CSS 变量（`bg-surface`、`text-accent` 等），不直接写颜色值。主题切换 = 切换 `html[data-theme]` 属性 = 一套变量值换成另一套，一帧内完成，零闪烁。

| 主题 | 代号 | 风格 | 状态 |
|------|------|------|------|
| 经典蓝 | `classic` | 当前冷蓝色风格，1:1 映射现有颜色 | **默认** |
| Claude 暖色 | `claude` | 羊皮纸底+赤陶色 accent+暖灰 | 可选 |
| （未来可扩展更多主题） | — | — | — |

**切换机制**：`<html data-theme="classic">` 或 `<html data-theme="claude">`
- 用户在设置面板选择，存入 localStorage
- `useTheme` Hook 管理状态

### 7.1 主题 CSS（`theme.css`）

```css
@theme {
  /* ========== 字体 ========== */
  /* classic 主题：纯 sans-serif（和现有一致） */
  /* claude 主题：标题 serif + 正文 sans-serif */
  /* 默认用 sans，claude 主题下覆盖 heading 字体 */
  --font-heading: -apple-system, BlinkMacSystemFont, "PingFang SC",
                  "Hiragino Sans GB", "Microsoft YaHei",
                  system-ui, sans-serif;
  --font-body: -apple-system, BlinkMacSystemFont, "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei",
               system-ui, sans-serif;
  --font-code: "SF Mono", "JetBrains Mono", ui-monospace,
               Menlo, Consolas, monospace;

  /* ========== 圆角（主题共享） ========== */
  --radius-sm: 6px;       /* 小按钮、标签 */
  --radius-md: 8px;       /* 标准按钮、卡片 */
  --radius-lg: 12px;      /* 主按钮、输入框 */
  --radius-xl: 16px;      /* 大卡片、视频容器 */
  --radius-2xl: 24px;     /* 消息气泡 */
  --radius-full: 9999px;  /* 头像、徽章 */

  /* ========== Z-index 层级体系（主题共享） ========== */
  --z-base: 0;           /* 默认 */
  --z-sticky: 10;        /* sticky 元素（CategoryTabs） */
  --z-header: 20;        /* 顶部导航栏 */
  --z-dropdown: 30;      /* 下拉菜单、popup */
  --z-overlay: 40;       /* 遮罩层 */
  --z-modal: 50;         /* Modal/Dialog */
  --z-toast: 60;         /* Toast 通知 */
  --z-error: 9999;       /* ErrorBoundary 崩溃屏 */

  /* ========== 动画时长（主题共享） ========== */
  --duration-instant: 50ms;   /* 微交互（tooltip） */
  --duration-fast: 100ms;     /* 快速反馈 */
  --duration-normal: 150ms;   /* 常规过渡 */
  --duration-moderate: 200ms; /* 进入动画 */
  --duration-slow: 250ms;     /* 强调动画 */
  --duration-slower: 350ms;   /* 页面级过渡 */

  /* ========== 缓动函数（主题共享） ========== */
  --ease-out: cubic-bezier(0.25, 0.46, 0.45, 0.94);       /* 自然减速 */
  --ease-in: cubic-bezier(0.55, 0.06, 0.68, 0.19);        /* 自然加速 */
  --ease-spring: cubic-bezier(0.32, 0.72, 0, 1);           /* 苹果弹性（核心） */
  --ease-bounce: cubic-bezier(0.34, 1.56, 0.64, 1);        /* 弹跳 */
  --ease-smooth: cubic-bezier(0.4, 0, 0.2, 1);             /* 平滑（Material） */
}

/* ==========================================================
 * 主题 1：经典蓝（默认）
 * 1:1 映射现有 Tailwind 颜色，迁移后视觉零变化
 * ========================================================== */

:root,
html[data-theme="classic"] {
  /* 品牌色 */
  --color-accent: #2563eb;           /* blue-600 */
  --color-accent-hover: #1d4ed8;     /* blue-700 */
  --color-accent-light: #dbeafe;     /* blue-100 */

  /* 表面/背景 */
  --color-surface: #f9fafb;          /* gray-50 */
  --color-surface-card: #ffffff;     /* white */
  --color-surface-elevated: #ffffff; /* white */
  --color-surface-dark: #1f2937;     /* gray-800 */
  --color-surface-dark-card: #374151;/* gray-700 */

  /* 文字 */
  --color-text-primary: #111827;     /* gray-900 */
  --color-text-secondary: #374151;   /* gray-700 */
  --color-text-tertiary: #6b7280;    /* gray-500 */
  --color-text-disabled: #9ca3af;    /* gray-400 */
  --color-text-on-dark: #ffffff;
  --color-text-on-accent: #ffffff;

  /* 边框 */
  --color-border-default: #e5e7eb;   /* gray-200 */
  --color-border-light: #f3f4f6;     /* gray-100 */
  --color-border-dark: #374151;      /* gray-700 */

  /* 交互 */
  --color-hover: #f3f4f6;            /* gray-100 */
  --color-active: #e5e7eb;           /* gray-200 */
  --color-focus-ring: #3b82f6;       /* blue-500 */

  /* 语义色 */
  --color-success: #16a34a;          /* green-600 */
  --color-success-light: #f0fdf4;    /* green-50 */
  --color-error: #dc2626;            /* red-600 */
  --color-error-light: #fef2f2;      /* red-50 */
  --color-warning: #d97706;          /* amber-600 */
  --color-warning-light: #fffbeb;    /* amber-50 */

  /* 阴影（标准冷灰） */
  --shadow-ring: 0 0 0 1px rgba(0, 0, 0, 0.05);
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);

  /* 用户消息气泡渐变（保留现有紫色） */
  --color-user-bubble-from: #a855f7; /* purple-500 */
  --color-user-bubble-to: #6366f1;   /* indigo-500 */
}

/* ==========================================================
 * 主题 2：Claude 暖色
 * 参考 Claude DESIGN.md —— 羊皮纸底+赤陶色+全系暖灰
 * ========================================================== */

html[data-theme="claude"] {
  /* 字体：标题改用 serif（Claude 标志性） */
  --font-heading: Georgia, "Noto Serif SC", "PingFang SC",
                  "Hiragino Sans GB", serif;

  /* 品牌色（赤陶色） */
  --color-accent: #c96442;
  --color-accent-hover: #b85a3a;
  --color-accent-light: #f5ebe6;

  /* 表面/背景（暖色羊皮纸） */
  --color-surface: #f5f4ed;          /* Parchment */
  --color-surface-card: #faf9f5;     /* Ivory */
  --color-surface-elevated: #ffffff;
  --color-surface-dark: #141413;     /* Near Black */
  --color-surface-dark-card: #30302e;/* Dark Surface */

  /* 文字（全系暖灰） */
  --color-text-primary: #141413;     /* Anthropic Near Black */
  --color-text-secondary: #5e5d59;   /* Olive Gray */
  --color-text-tertiary: #87867f;    /* Stone Gray */
  --color-text-disabled: #b0aea5;    /* Warm Silver */
  --color-text-on-dark: #faf9f5;     /* Ivory */
  --color-text-on-accent: #faf9f5;

  /* 边框（暖奶油色） */
  --color-border-default: #e8e6dc;   /* Border Warm */
  --color-border-light: #f0eee6;     /* Border Cream */
  --color-border-dark: #30302e;

  /* 交互 */
  --color-hover: rgba(0, 0, 0, 0.04);
  --color-active: rgba(0, 0, 0, 0.08);
  --color-focus-ring: #3898ec;       /* Focus Blue（唯一冷色） */

  /* 语义色（Claude 偏暖） */
  --color-success: #16a34a;
  --color-success-light: #f0fdf4;
  --color-error: #b53333;            /* 暖红，比标准 red-600 更深沉 */
  --color-error-light: #fef2f2;
  --color-warning: #d97706;
  --color-warning-light: #fffbeb;

  /* 阴影（暖色调 ring shadow，Claude 标志性） */
  --shadow-ring: 0 0 0 1px #f0eee6;
  --shadow-sm: 0 1px 2px rgba(20, 20, 19, 0.05);
  --shadow-md: 0 4px 12px rgba(20, 20, 19, 0.08);
  --shadow-lg: 0 8px 24px rgba(20, 20, 19, 0.12);
  --shadow-xl: 0 16px 48px rgba(20, 20, 19, 0.16);

  /* 用户消息气泡（赤陶色渐变） */
  --color-user-bubble-from: #c96442;
  --color-user-bubble-to: #a85535;
}

/* ==========================================================
 * Dark 模式（每个主题各有一套 dark 值）
 * Phase 4 实施，此处定义结构预留
 * ========================================================== */

html[data-theme="classic"].dark {
  color-scheme: dark;
  --color-surface: #111827;         /* gray-900 */
  --color-surface-card: #1f2937;    /* gray-800 */
  --color-surface-elevated: #374151;/* gray-700 */
  --color-text-primary: #f9fafb;    /* gray-50 */
  --color-text-secondary: #d1d5db;  /* gray-300 */
  --color-text-tertiary: #9ca3af;   /* gray-400 */
  --color-text-disabled: #6b7280;   /* gray-500 */
  --color-border-default: #374151;  /* gray-700 */
  --color-border-light: #1f2937;    /* gray-800 */
  --color-hover: rgba(255,255,255,0.06);
  --color-active: rgba(255,255,255,0.1);
  --color-accent-light: #1e3a5f;
}

html[data-theme="claude"].dark {
  color-scheme: dark;
  --color-surface: #141413;
  --color-surface-card: #1e1e1c;
  --color-surface-elevated: #30302e;
  --color-text-primary: #faf9f5;
  --color-text-secondary: #b0aea5;
  --color-text-tertiary: #87867f;
  --color-text-disabled: #5e5d59;
  --color-border-default: #30302e;
  --color-border-light: #3d3d3a;
  --color-hover: rgba(255,255,255,0.06);
  --color-active: rgba(255,255,255,0.1);
  --color-accent-light: #2a1f1a;
}

/* ========== 减少动画偏好 ========== */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

### 7.1.1 主题切换效果对比

| Token | 经典蓝（classic） | Claude 暖色（claude） |
|-------|------------------|---------------------|
| `bg-surface` | `#f9fafb` 冷灰底 | `#f5f4ed` 羊皮纸 |
| `bg-surface-card` | `#ffffff` 纯白 | `#faf9f5` 象牙白 |
| `bg-accent` | `#2563eb` 蓝色 | `#c96442` 赤陶色 |
| `text-text-primary` | `#111827` 冷黑 | `#141413` 暖黑 |
| `text-text-secondary` | `#374151` 冷灰 | `#5e5d59` 暖灰 |
| `border-border-default` | `#e5e7eb` 冷灰线 | `#e8e6dc` 奶油线 |
| `shadow-md` | 标准投影 | 暖色投影 |
| 用户气泡 | 紫→靛蓝渐变 | 赤陶色渐变 |
| 标题字体 | Sans-serif | Serif（Georgia） |

### 7.2 动画系统（`animations.css`）

**设计原则（苹果级丝滑）：**

| 原则 | 说明 |
|------|------|
| 进入慢、退出快 | 进入 200-250ms，退出 150ms（苹果标准） |
| spring 缓动为主 | `cubic-bezier(0.32, 0.72, 0, 1)` 是苹果系统的核心缓动 |
| 属性最小化 | 只动 `transform` + `opacity`，不动 `width/height/margin`（GPU 加速）。**例外**：`message-out` 删除动画需要 `max-height` 收缩，频率低可接受 |
| `will-change` 精准 | 仅在动画播放时添加，动画结束后移除 |
| 无 box-shadow 动画 | 阴影变化用 `::after` 伪元素 opacity 切换（性能好 10x） |

**动画分类：**

```css
/* ============ 1. 淡入/淡出（基础） ============ */

@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes fade-out {
  from { opacity: 1; }
  to { opacity: 0; }
}
.animate-fade-in {
  animation: fade-in var(--duration-fast) var(--ease-out);
}

/* ============ 2. 下拉菜单（Dropdown） ============ */

@keyframes dropdown-enter {
  from { opacity: 0; transform: translateY(-6px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes dropdown-exit {
  from { opacity: 1; transform: translateY(0) scale(1); }
  to   { opacity: 0; transform: translateY(-6px) scale(0.97); }
}
.animate-dropdown-enter {
  animation: dropdown-enter var(--duration-normal) var(--ease-spring);
}
.animate-dropdown-exit {
  animation: dropdown-exit var(--duration-fast) var(--ease-in);
}

/* ============ 3. 弹出菜单（Popup，从下往上） ============ */

@keyframes popup-enter {
  from { opacity: 0; transform: translateY(4px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes popup-exit {
  from { opacity: 1; transform: translateY(0) scale(1); }
  to   { opacity: 0; transform: translateY(4px) scale(0.97); }
}
.animate-popup-enter {
  animation: popup-enter var(--duration-normal) var(--ease-spring);
}
.animate-popup-exit {
  animation: popup-exit var(--duration-fast) var(--ease-in);
}

/* ============ 4. Modal 弹框 ============ */

@keyframes modal-enter {
  from { opacity: 0; transform: scale(0.95) translateY(10px); }
  to   { opacity: 1; transform: scale(1) translateY(0); }
}
@keyframes modal-exit {
  from { opacity: 1; transform: scale(1) translateY(0); }
  to   { opacity: 0; transform: scale(0.95) translateY(10px); }
}
.animate-modal-enter {
  animation: modal-enter var(--duration-moderate) var(--ease-spring);
}
.animate-modal-exit {
  animation: modal-exit var(--duration-normal) var(--ease-in) forwards;
}

/* 遮罩层 */
@keyframes backdrop-enter {
  from { opacity: 0; }
  to   { opacity: 1; }
}
@keyframes backdrop-exit {
  from { opacity: 1; }
  to   { opacity: 0; }
}
.animate-backdrop-enter {
  animation: backdrop-enter var(--duration-moderate) var(--ease-out);
}
.animate-backdrop-exit {
  animation: backdrop-exit var(--duration-normal) var(--ease-in) forwards;
}

/* ============ 5. Drawer 抽屉（从右滑入） ============ */

@keyframes drawer-enter {
  from { transform: translateX(100%); }
  to   { transform: translateX(0); }
}
@keyframes drawer-exit {
  from { transform: translateX(0); }
  to   { transform: translateX(100%); }
}
.animate-drawer-enter {
  animation: drawer-enter var(--duration-slow) var(--ease-spring);
}
.animate-drawer-exit {
  animation: drawer-exit var(--duration-normal) var(--ease-in) forwards;
}

/* ============ 6. 消息动画 ============ */

/* 用户消息（从输入框向上滑入） */
@keyframes message-in {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
.animate-message-in {
  animation: message-in var(--duration-slow) var(--ease-spring);
  will-change: transform, opacity;
}

/* AI 消息（淡入 + 微缩放） */
@keyframes message-ai-in {
  from { opacity: 0; transform: scale(0.98); }
  to   { opacity: 1; transform: scale(1); }
}
.animate-message-ai-in {
  animation: message-ai-in var(--duration-moderate) var(--ease-spring);
  will-change: transform, opacity;
}

/* 消息删除（向下滑出 + 收缩） */
@keyframes message-out {
  from { opacity: 1; transform: translateY(0); max-height: 500px; }
  to   { opacity: 0; transform: translateY(8px); max-height: 0; margin: 0; padding: 0; }
}
.animate-message-out {
  animation: message-out var(--duration-normal) var(--ease-in) forwards;
  overflow: hidden;
}

/* ============ 7. 循环动画 ============ */

/* 上传呼吸 */
@keyframes breathe {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.7; transform: scale(1.04); }
}
.animate-breathe {
  animation: breathe 1.2s var(--ease-smooth) infinite;
}

/* 打字光标 */
@keyframes cursor-blink {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0; }
}
.animate-cursor-blink {
  animation: cursor-blink 0.6s ease-in-out infinite;
}

/* 绿点呼吸（通知） */
@keyframes dot-pulse {
  0%, 100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(34,197,94,0.5); }
  50%      { transform: scale(1.2); box-shadow: 0 0 0 4px rgba(34,197,94,0); }
}
.animate-dot-pulse {
  animation: dot-pulse 1.8s var(--ease-smooth) infinite;
}

/* 媒体加载脉冲 */
@keyframes media-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.5; }
}
.animate-media-pulse {
  animation: media-pulse 1.5s var(--ease-smooth) infinite;
}

/* 思考中省略号 */
@keyframes thinking-bounce {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40%           { opacity: 1; transform: scale(1); }
}

/* 思考内容展开 */
@keyframes thinking-expand {
  from { opacity: 0; max-height: 0; }
  to   { opacity: 1; max-height: 2000px; }
}

/* 思考闪烁渐变 */
@keyframes thinking-shimmer {
  0%, 100% { background-position: 200% 0; }
  50%      { background-position: -200% 0; }
}

/* ============ 8. 通用 Transition 工具类 ============ */

.transition-base {
  transition-property: background-color, border-color, color, opacity;
  transition-duration: var(--duration-normal);
  transition-timing-function: var(--ease-out);
}

.transition-transform {
  transition-property: transform, opacity;
  transition-duration: var(--duration-moderate);
  transition-timing-function: var(--ease-spring);
}

.transition-shadow {
  transition-property: box-shadow;
  transition-duration: var(--duration-normal);
  transition-timing-function: var(--ease-out);
}
```

### 7.3 旧动画名 → 新动画名映射

迁移时需要全局替换的 class name：

| 旧 class | 新 class | 备注 |
|----------|----------|------|
| `animate-fadeIn` | `animate-fade-in` | 统一 kebab-case |
| `animate-slideDown` | `animate-dropdown-enter` | 语义化 |
| `animate-slideUp` | `animate-dropdown-exit` | 语义化 |
| `animate-popupEnter` | `animate-popup-enter` | kebab-case |
| `animate-popupExit` | `animate-popup-exit` | kebab-case |
| `animate-modalEnter` | `animate-modal-enter` | kebab-case |
| `animate-modalExit` | `animate-modal-exit` | kebab-case |
| `animate-backdropEnter` | `animate-backdrop-enter` | kebab-case |
| `animate-backdropExit` | `animate-backdrop-exit` | kebab-case |
| `animate-drawerSlideIn` | `animate-drawer-enter` | 语义化 |
| `animate-drawerSlideOut` | `animate-drawer-exit` | 语义化 |
| `animate-upload-glow` | `animate-breathe` | 通用化 |
| `animate-slide-up` | `animate-modal-enter` | 合并相似动画 |
| `animate-message-slide-in` | `animate-message-in` | 简化 |
| `animate-ai-message-fade-scale` | `animate-message-ai-in` | 简化 |
| `animate-message-slide-out` | `animate-message-out` | 简化 |
| `animate-typing-cursor` | `animate-cursor-blink` | 语义化 |
| `animate-dot-breathe` | `animate-dot-pulse` | 语义化 |
| `animate-media-pulse` | `animate-media-pulse` | 保持不变 |

### 7.4 组件库设计

#### Button

```tsx
interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'accent' | 'secondary' | 'dark' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  icon?: React.ReactNode;
}
```

| variant | 背景 | 文字 | hover | active | 对应旧样式 |
|---------|------|------|-------|--------|-----------|
| accent | `accent` | `text-on-accent` | `accent-hover` | 加深 5% | `bg-blue-600 text-white` |
| secondary | `surface-card` | `text-primary` | 加深 | 再加深 | `bg-gray-100 text-gray-700` |
| dark | `surface-dark-card` | `text-on-dark` | 加亮 | 再加亮 | 新增 |
| ghost | 透明 | `text-secondary` | `hover` | `active` | `text-gray-600 hover:bg-gray-100` |
| danger | 透明 | `error` | `error-light` | 加深 | `text-red-600 hover:bg-red-50` |

**交互状态（所有 variant 统一）：**
- `focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2` — 键盘导航可见，鼠标点击不显示
- `active:scale-[0.98]` — 按下微缩（苹果风格触感反馈）
- `disabled:opacity-50 disabled:pointer-events-none`

| size | padding | font-size | 圆角 |
|------|---------|-----------|------|
| sm | `px-3 py-1.5` | `text-sm` | `radius-sm` |
| md | `px-4 py-2` | `text-sm` | `radius-md` |
| lg | `px-5 py-2.5` | `text-base` | `radius-lg` |

#### Input

```tsx
interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  icon?: React.ReactNode;
}
```

- 背景：`surface-elevated`
- 边框：`border-default`，focus 时 `focus-ring`
- 圆角：`radius-lg`（12px）
- Ring shadow 代替粗 border 变化（Claude 风格）

#### Card

```tsx
interface CardProps {
  variant?: 'default' | 'elevated' | 'interactive';
  padding?: 'sm' | 'md' | 'lg';
  children: React.ReactNode;
}
```

- default：`surface-card` + `border-light` + `shadow-ring`
- elevated：`surface-elevated` + `shadow-md`
- interactive：default + hover 时 `shadow-md` + `translateY(-1px)`（苹果卡片悬浮效果）

#### Badge

```tsx
interface BadgeProps {
  variant?: 'default' | 'accent' | 'success' | 'error' | 'warning';
  size?: 'sm' | 'md';
  children: React.ReactNode;
}
```

#### Dropdown

```tsx
interface DropdownProps {
  trigger: React.ReactNode;
  children: React.ReactNode;
  placement?: 'top' | 'bottom';
}
```

- 内置 `animate-dropdown-enter` / `animate-dropdown-exit` 动画
- 内置 `useModalAnimation` 状态管理
- Ring shadow 边框（`0 0 0 1px`，Claude 风格）

### 7.5 useTheme Hook

```tsx
interface ThemeState {
  /** 主题风格 */
  theme: 'classic' | 'claude';
  /** 明暗模式（Phase 4） */
  colorMode: 'light' | 'dark' | 'system';
  /** 实际生效的明暗 */
  isDark: boolean;
  /** 切换主题风格 */
  setTheme: (theme: 'classic' | 'claude') => void;
  /** 切换明暗模式 */
  setColorMode: (mode: 'light' | 'dark' | 'system') => void;
}
```

- `data-theme` 属性控制主题风格（`classic` / `claude`）
- `.dark` / `.light` class 控制明暗模式
- localStorage keys：`everydayai_theme`（风格）+ `everydayai_color_mode`（明暗）
- 初始化：在 `index.html` 的 `<script>` 中同步读取并设置属性（防闪白/闪色）
- 默认值：`theme = 'classic'`，`colorMode = 'system'`

## 8. 开发任务拆分

### Phase 1：Token 基建 + 多主题（纯新增，零破坏）— B 级

- [ ] 1.1 创建 `styles/theme.css`（@theme 共享 token + z-index 体系 + classic/claude 主题变量 + dark 预留 + `@variant dark` 配置 + `::selection` 主题化 + 滚动条主题化）
- [ ] 1.2 创建 `styles/animations.css`（全部动画 keyframes + utility class）
- [ ] 1.3 改造 `index.css`（精简为 3 行 import）
- [ ] 1.4 `index.html` 加主题初始化脚本（读 localStorage → 设置 data-theme + class，防闪白/闪色）
- [ ] 1.5 创建 `useTheme.ts` Hook（管理 theme + colorMode）
- [ ] 1.6 创建 `utils/cn.ts`（className 条件拼接工具函数）
- [ ] 1.7 验证：新 token class 可用（`bg-surface`、`text-accent` 等），旧 class 不受影响
- [ ] 1.8 验证：手动切换 `data-theme="claude"` 后颜色正确切换
- [ ] 1.9 App.tsx 挂载 `<Toaster />` 组件（修复既有 bug：toast 通知从未显示）+ 传入 CSS 变量样式
- [ ] 1.10 单元测试

### Phase 2：组件库（纯新增，零破坏）— A 级

- [ ] 2.1 创建 `Button.tsx`（5 variant × 3 size + loading + icon + active/focus-visible）
- [ ] 2.2 创建 `Input.tsx`（label + error + icon + focus-visible）
- [ ] 2.3 创建 `Card.tsx`（3 variant）
- [ ] 2.4 创建 `Badge.tsx`（5 variant × 2 size）
- [ ] 2.5 创建 `Dropdown.tsx`（内置动画 + 定位 + placement）
- [ ] 2.6 改造 `Modal.tsx`（用 token + 新动画 class + z-index token）
- [ ] 2.7 组件单元测试

### Phase 3：逐模块迁移（渐进式替换）— A 级

按模块优先级迁移，每个子任务独立可发布：

- [ ] 3.1 **页面根背景**：Chat.tsx + Home.tsx + ForgotPassword.tsx（`bg-gray-50` → `bg-surface`）
- [ ] 3.2 **ErrorBoundary.tsx**：11 处内联样式 → Tailwind token class + z-index token
- [ ] 3.3 **ChatHeader + Sidebar**：换色 + 换 Button 组件 + z-index token
- [ ] 3.4 **InputArea + InputControls**：换色 + 换 Input 组件
- [ ] 3.5 **MessageItem + MessageArea**：用户气泡 → CSS 变量 + 动画 class 迁移
- [ ] 3.6 **统一 Modal 实现**：DeleteConfirmModal + DeleteMessageModal + TableExportModal → 改用 common/Modal；ImagePreviewModal + FilePreviewModal → 内联 keyframe 迁移到 animations.css；ModelDetailDrawer → 复用 useModalAnimation
- [ ] 3.7 **统一菜单实现**：ModelSelector + DropdownMenu + ContextMenu + ImageContextMenu + UploadMenu + AdvancedSettingsMenu → 换 Dropdown 组件或统一定位/动画方式
- [ ] 3.8 **Home 页面**：NavBar + HeroSection + ModelCard + CategoryTabs
- [ ] 3.9 **Auth 页面**：LoginForm + RegisterForm + ForgotPassword
- [ ] 3.10 **Admin 页面**：AdminPanel + OrgManagePanel + SuperAdminPanel
- [ ] 3.11 **markdown.css**：硬编码色值 → CSS 变量 + 迁移 thinking 动画到 animations.css（去重 3 个 @keyframes）
- [ ] 3.12 **内联 SVG 清理**：Modal.tsx/DeleteConfirmModal/MessageActions 等 15+ 处内联 SVG → lucide-react
- [ ] 3.13 **className 拼接优化**：高频组件的模板字符串 → `cn()` 工具函数
- [ ] 3.14 动画 class 全局替换（旧名 → 新名，见 7.3 映射表）+ 同步更新 `useMessageAnimation.ts` 硬编码的 2 个 class 名 + 更新 `ImageContextMenu.test.tsx` / `ImagePreview.test.tsx` 中 6 个样式断言
- [ ] 3.15 清理 `index.css` 中旧的 @keyframes 定义
- [ ] 3.16 全量测试 + TypeScript 检查

### Phase 4：主题选择 UI + Dark 模式 — B 级

- [ ] 4.1 SettingsModal 加主题选择器（经典蓝 / Claude 暖色，带颜色预览圆点）
- [ ] 4.2 SettingsModal 加明暗模式切换（浅色/深色/跟随系统）
- [ ] 4.3 逐组件补全 dark 变量适配（大部分已通过 CSS 变量自动完成）
- [ ] 4.4 markdown.css dark 适配
- [ ] 4.5 Toaster（react-hot-toast）样式跟随主题色调
- [ ] 4.6 主题切换过渡动画（临时添加 `html.theme-transitioning` class，300ms 全局 transition）
- [ ] 4.7 全量测试

### Phase 5：目录结构整理 — B 级

- [ ] 5.1 chat/ 目录拆分：modals/（7 个）、menus/（5 个）、media/（8 个）、message/（6 个）
- [ ] 5.2 全局搜索更新所有 import 路径（grep 原路径确认零残留）
- [ ] 5.3 全量测试 + TypeScript 检查

## 9. 依赖变更

无需新增依赖。

现有依赖完全满足需求：
- Tailwind CSS 4.1 的 `@theme` 指令 ✅
- Lucide React 图标 ✅
- React 19 ✅

## 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| Phase 3 大规模 class 替换引入 UI 回归 | 高 | 每个子任务独立提交，逐模块验证；保留旧 class 可用直到全部迁移完 |
| @theme 中自定义 token 与 Tailwind 默认 class 冲突 | 中 | token 命名避开 Tailwind 默认（如用 `accent` 不用 `blue`，用 `surface` 不用 `white`）。**已 PoC 验证**：Tailwind v4 生成 `var()` 引用而非内联值，运行时覆盖有效 |
| Dark 模式下第三方库样式不适配 | 低 | highlight.js/KaTeX/mermaid 独立处理，不纳入 token |
| 动画 class 重命名遗漏 | 中 | grep 全量搜索旧名，确认零残留再删除旧定义 |
| 移动端动画卡顿 | 低 | 仅用 transform+opacity（GPU 层），prefers-reduced-motion 降级 |
| 6 个自定义 Modal 统一时破坏交互 | 中 | 逐个迁移，ImagePreviewModal/FilePreviewModal 保持独立布局只迁移动画 |
| Phase 5 目录拆分导致大量 import 路径变更 | 高 | grep 原路径确认零残留，拆分后全量 tsc --noEmit 检查 |
| cn() 引入后 className 格式变化 | 低 | cn() 兼容字符串，渐进替换，不影响已有模板字符串 |

## 11. 文档更新清单

- [ ] `docs/document/PROJECT_OVERVIEW.md` — 新增 styles/ 目录说明
- [ ] `docs/document/FUNCTION_INDEX.md` — 新增 ui 组件和 useTheme Hook

## 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（Phase 3 覆盖 47 个文件）
- [x] 7 类边界场景均有处理策略（见第 2 节）
- [x] 所有新增文件预估 ≤ 500 行
- [x] 无新增依赖
- [x] 动画命名统一 kebab-case
- [x] 旧新 class 共存策略确保零破坏迁移
- [x] Dark 模式防闪白方案（index.html 同步脚本）
- [x] `prefers-reduced-motion` 无障碍支持
- [x] @theme 运行时覆盖可行性已 PoC 验证
- [x] ErrorBoundary 内联样式纳入迁移（3.2）
- [x] 6 个自定义 Modal 统一纳入迁移（3.6）
- [x] 6 个菜单组件统一纳入迁移（3.7）
- [x] 15+ 处内联 SVG 清理纳入迁移（3.12）
- [x] cn() 工具函数纳入 Phase 1（1.6）
- [x] z-index 体系纳入 @theme token
- [x] focus-visible/active 状态纳入 Button 组件设计
- [x] chat/ 目录拆分纳入 Phase 5（5.1-5.3）
- [x] `@variant dark` 配置确保 26 处已有 dark: 前缀生效
- [x] `<Toaster />` 未挂载 bug 发现并纳入 Phase 1（1.9）
- [x] CSS 渐变 + CSS 变量方案确认（`from-[var()]` 语法）
- [x] `/50` opacity 修饰符兼容性确认（无需特殊处理）
- [x] `::selection` 和滚动条主题化纳入 Phase 1（1.1）
- [x] 主题切换过渡动画纳入 Phase 4（4.6）

## 13. 已完成与遗留事项

### Phase 3 实际完成范围（V2 - 2026-04-10）

| 子任务 | 完成度 | 说明 |
|--------|------|------|
| 3.1 页面根背景 | ✅ 100% | Chat.tsx + Home.tsx 完成 |
| 3.2 ErrorBoundary | ✅ 100% | 11 处内联 style → token class |
| 3.3 ChatHeader + Sidebar | ✅ 100% | 全 token 化 + 7 处内联 SVG → lucide |
| 3.4 InputArea + InputControls | ✅ 100% | 18 处颜色 token 化 |
| 3.5 MessageItem + MessageArea | ✅ 100% | 用户气泡 → CSS 变量 + 2 处 SVG → lucide |
| 3.6 统一 Modal | ⚠️ 部分 | 已完成 DeleteConfirm/DeleteMessage/TableExport；ImagePreviewModal/FilePreviewModal/ModelDetailDrawer 保留独立实现 |
| 3.7 统一菜单 | ✅ 100% | 6 个菜单全部 token 化 + ModelSelector 8 处 SVG → lucide |
| 3.8 Home + ModelDetailDrawer | ✅ 100% | 8 个文件全 token 化 |
| 3.9 Auth 页面 | ✅ 100% | 4 个文件，80+ 处样式替换 |
| 3.10 Admin 页面 | ✅ 100% | 5 个文件，100+ 处样式替换 |
| 3.11 markdown.css | ✅ 100% | 13 处 hex → CSS 变量 |
| 3.12 内联 SVG | ⚠️ 部分 | 已清理 ~30 处（核心组件）；剩余 45 处见下表 |

### 遗留事项 1：剩余内联 SVG（45 处）

**不影响主题切换功能**，仅代码风格优化。可单独开任务清理。

| 文件 | SVG 数 | 性质 |
|------|--------|------|
| `chat/MessageActions.tsx` | 9 | 工具栏按钮（复制/重新生成/删除/分享等） |
| `chat/SettingsModal.tsx` | 6 | 设置项图标 |
| `auth/RegisterForm.tsx` | 5 | 表单状态图标 |
| `chat/MessageMedia.tsx` | 3 | 媒体占位符 |
| `chat/ImagePreview.tsx` | 3 | 预览操作按钮 |
| `auth/LoginForm.tsx` | 3 | 表单状态图标 |
| `chat/FilePreview.tsx` | 2 | 文件操作按钮 |
| `chat/AiImageGrid.tsx` | 2 | 图片占位符 |
| `chat/AudioPreview.tsx` | 2 | 播放控制 |
| `chat/AdvancedSettingsMenu.tsx` | 2 | 设置项图标 |
| `chat/CodeBlock.tsx` | 2 | 代码块工具 |
| `pages/WecomCallback.tsx` | 2 | 状态图标 |
| `chat/ThinkingBlock.tsx` | 1 | 折叠箭头 |
| `chat/FileCard.tsx` | 1 | 文件类型图标 |
| `auth/WecomQrLogin.tsx` | 1 | 状态图标 |
| `admin/AdminPanel.tsx` | 1 | 操作图标 |

**注意保留项：**
- `chat/ConversationItem.tsx` 的 `WecomIcon`（企微品牌图标，lucide 无对应）
- `chat/ImagePreviewModal.tsx` / `FilePreviewModal.tsx`（黑底白字独立预览界面，不跟随主题）

### 遗留事项 2：未统一的独立 Modal

`ImagePreviewModal.tsx` (471 行) 和 `FilePreviewModal.tsx` (264 行) **未改用 common/Modal**。

**原因**：它们是黑底白字的全屏图片/文件预览，UI 风格独立于主题（不跟随 light/dark/classic/claude），强制套 common/Modal 反而会破坏其特殊布局（createPortal + 自定义 keyframes + 缩放/拖拽逻辑）。

**保留现状** 是合理的设计权衡，无需后续处理。

### 遗留事项 3：Phase 3.13 - 3.16 待执行

- 3.13 className 拼接 → cn() 工具迁移（高频组件）
- 3.14 动画 class 全局替换（旧名 → 新名 + 测试断言更新）
- 3.15 清理 index.css 中旧 keyframes 兼容层
- 3.16 全量回归测试 + 构建验证

---

## V3 — 大厂级动效+组件+3 主题系统升级（2026-04-11）

> 本节追加于 V2 (Phase 5 - chat/ 目录拆分) 之后，独立大章节。
> 目标：全站引入 framer-motion + Radix + cva + 3 层 token + 第 3 主题（Linear）+ 路由懒加载。

### 参考资料

- https://github.com/wucong75644-crypto/awesome-design-md
- `design-md/claude/DESIGN.md` — Claude 主题完整落地参考
- `design-md/linear.app/DESIGN.md` — Linear 主题完整落地参考

### 目标

1. 动效：全站 framer-motion spring/layout/gesture，苹果级丝滑
2. 组件：Radix UI 底座（a11y + portal + 键盘）+ cva variants（类型安全）
3. 主题：第 3 主题 Linear 新增 + Claude 按 DESIGN.md 补齐 + classic 微升级
4. 架构：修复 3 个架构隐患 — Portal / 虚拟滚动 / 路由懒加载
5. 动画质量："更好看 + 更丝滑 + 更不生硬"

### 技术决策

| 项 | 选择 |
|---|---|
| 动画库 | framer-motion@^12 + LazyMotion + domAnimation（tree-shake） |
| 基础组件 | @radix-ui/react-{dialog,dropdown-menu,popover,tooltip} |
| Variants API | class-variance-authority (cva) |
| 虚拟滚动 | ❌ 保留不做（与现有 use-stick-to-bottom 冲突，历史踩坑） |
| Bundle 预算 | +50KB gzip（含 framer 35KB + radix 15KB） |
| Storybook | ❌ 不加（超范围） |

### 14 Phase 执行结果

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 装依赖 + 3 层 token + motion/glass util + vitest motion-mock + main.tsx LazyMotion | ✅ |
| 1 | Claude 主题按 DESIGN.md 完整补齐（serif + ring shadow + 32px 圆角 + 暖色阶） | ✅ |
| 2 | Linear 主题新增（Inter cv01,ss03 + 510 weight + 近黑 + 靛紫 + 透明分层） | ✅ |
| 3 | classic 主题微升级（圆角 +4px + ring shadow 变体 + whisper） | ✅ |
| 4 | Radix + cva 基础层（Dialog/DropdownMenu/Popover/Tooltip + variants 工具） | ✅ |
| 5 | ui/ 5 组件重做（Button/Card/Input/Badge/Dropdown 走 cva + framer） | ✅ |
| 6 | motion/ 原语库（Reveal/PageTransition/Stagger/MagneticButton/LayoutTransition） | ✅ |
| 7 | common/Modal 换 Radix 底座（🔧 隐患 2）+ 会话列表 FLIP 动画 | ✅ |
| 8 | 消息区 layout 动画 + 气泡内高光 + ThinkingBlock spring（🔧 隐患 1 保留） | ⚠️ 部分 |
| 9 | ModelSelector Magic Move + 发送按钮 spring hover/tap | ✅ |
| 10 | 首页 HeroSection 磁吸 + CategoryTabs Magic Move + ModelCard 3D hover + NavBar glass | ✅ |
| 11 | auth Login/Register crossfade | ✅ |
| 12 | 路由懒加载 React.lazy + AnimatePresence mode="wait"（🔧 隐患 3） | ✅ |
| 13 | Settings 主题选择器 3 卡片预览 + Magic Move 选中框 | ✅ |
| 14 | 全量测试 + bundle 对比 + 文档 + MEMORY 更新 | ✅ |

### 架构隐患处理

| 隐患 | 原方案 | 实际处理 |
|---|---|---|
| 1. 虚拟滚动缺失 | @tanstack/react-virtual 接管 MessageArea | ⚠️ 保留。use-stick-to-bottom 是完整封装方案，历史记录（2026-02-04 commit）明确从 Virtua 迁到 stick-to-bottom 解决滚动问题。强接会重踩坑。替代：MessageItem 用 layout="position" 只动位置不动尺寸，长对话动画性能可接受。 |
| 2. Modal 非 Portal | 换 Radix Dialog | ✅ Phase 7 完成。common/Modal 内部完全重写为 primitives/Dialog 薄封装，外部 API 100% 保留，6 个 Modal 使用者零修改 |
| 3. 路由非懒加载 | React.lazy + Suspense | ✅ Phase 12 完成。4 个 page 全部 lazy import。index 主 chunk 从 1432KB 降到 474KB（-67%） |

### Bundle 对比（收益）

| 指标 | V2（Phase 5 后） | V3（Phase 14 后） | 变化 |
|---|---|---|---|
| index.js 主 chunk（原始） | 1432.56 KB | 473.95 KB | **-67%** |
| index.js 主 chunk（gzip） | ~437 KB | ~154 KB | **-65%** |
| 首屏 JS 下载量（gzip） | ~437 KB | **~154 KB** | **-283 KB** |
| Chat chunk（独立，按需） | — | 964 KB | 按需 |
| Home chunk（独立，按需） | — | 22.7 KB | 按需 |

**关键洞察**：framer-motion (~35KB gzip) + Radix (~15KB gzip) + cva (~1KB gzip) **增量的 ~50KB 完全被懒加载省下的 283KB 覆盖**。
实际结果：**首屏 JS 净减少 ~233KB gzip**，动效/组件/主题全部升级却反而更快加载。

### 新增文件清单

**基建（Phase 0）**
- `styles/tokens/atoms.css` — 原子层 token（硬值）
- `styles/tokens/semantic.css` — 语义层 token（角色）
- `styles/tokens/component.css` — 组件层 token
- `styles/glass.css` — 毛玻璃工具类
- `utils/motion.ts` — framer spring preset + variants preset + gesture preset
- `utils/variants.ts` — cva re-export
- `test/motion-mock.ts` — jsdom IntersectionObserver/ResizeObserver polyfill + skipAnimations

**主题**
- `styles/themes/claude.css` — Claude 主题完整版
- `styles/themes/linear.css` — Linear 主题完整版

**Primitives（Radix 薄封装）**
- `components/primitives/Dialog.tsx`
- `components/primitives/DropdownMenu.tsx`
- `components/primitives/Popover.tsx`
- `components/primitives/Tooltip.tsx`
- `components/primitives/index.ts`
- `components/primitives/__tests__/*.test.tsx`（4 个测试文件，+26 测试）

**Motion 原语**
- `components/motion/Reveal.tsx`
- `components/motion/PageTransition.tsx`
- `components/motion/Stagger.tsx`
- `components/motion/MagneticButton.tsx`
- `components/motion/LayoutTransition.tsx`
- `components/motion/index.ts`
- `components/motion/__tests__/motion.test.tsx`（+15 测试）

### 测试覆盖

| 指标 | V2 | V3 |
|---|---|---|
| 测试总数 | 486 | **525** |
| 新增测试 | — | **+39**（Dialog×10 / DropdownMenu×8 / Popover×5 / Tooltip×3 / Motion×15） |
| 测试通过率 | 100% | 100% |
| tsc 零错 | ✅ | ✅ |
| vite build 通过 | ✅ | ✅ |

### 已知未做事项

- 虚拟滚动（与 use-stick-to-bottom 冲突，保留）
- ChatHeader useScroll 滚动联动（原 Phase 7 装饰性项）
- NavBar useScroll 收缩（部分做了毛玻璃，滚动收缩未做）
- ModelDetailDrawer 拖动关闭手势（原 Phase 7 装饰性项）
- UploadMenu/AdvancedSettingsMenu 换 Radix Popover（原 Phase 9 装饰性项）
- AiImageGrid stagger 进场（原 Phase 9 装饰性项）
- ImagePreviewModal 拖动关闭（原 Phase 9 装饰性项）
- admin/ErrorBoundary/LoadingScreen 动效升级（原 Phase 11 低频页面）
- ForgotPassword/WecomCallback 未包 PageTransition（Suspense fallback 已覆盖）
- Storybook 组件文档（超范围）

### 14 个 Commit 清单

```
7a215e3 Phase 0 — 基建
5bc01ba Phase 1 — Claude 主题补齐
3701f63 Phase 2 — Linear 主题新增
c796f8b Phase 3 — classic 主题微升级
08cd283 Phase 4 — Radix + cva 基础层
bf05863 Phase 5 — ui/ 5 核心组件重做
eb8069b Phase 6 — motion/ 原语库
8490fa7 Phase 7 — Modal 换 Radix + 会话列表 FLIP
e407c58 Phase 8 — 消息区 layout + 气泡高光
be687b4 Phase 9 — ModelSelector Magic Move
b74cbee Phase 10 — 首页动效升级
dcafa2d Phase 11 — auth crossfade
2b2774c Phase 12 — 路由懒加载 + 过渡
c1f9599 Phase 13 — Settings 3 卡片预览
```

### 下期可做

1. 虚拟滚动方案探索（找到与 use-stick-to-bottom 兼容的库或自研）
2. Storybook 组件文档 + Chromatic 视觉回归
3. admin 面板动效全面升级
4. ChatHeader/NavBar useScroll 滚动联动
5. 长按/拖动手势（图片预览/Drawer 拖动关闭）

# UI 设计方案：定时任务面板

> 版本：V1.0 | 日期：2026-04-09
> 设计基础：Linear 交互模式 + 现有项目颜色体系

---

## 一、设计原则

| 原则 | 说明 |
|------|------|
| **信息密度优先** | 参考 Linear 的列表密度，一屏展示尽量多的任务 |
| **状态一目了然** | 颜色 + 图标双通道传达状态，无需点击即可掌握全局 |
| **操作零摩擦** | 自然语言创建 + 手动编辑并存，选最快的方式 |
| **动画克制精致** | 所有动画服务于认知（帮用户理解变化），不服务于装饰 |
| **风格统一** | 与现有聊天界面、文件面板保持一致的蓝色科技风 |

---

## 二、颜色体系（复用现有）

### 基础色

```css
/* 主色 */
--color-primary:        #3b82f6;  /* blue-600，按钮、选中态 */
--color-primary-hover:  #2563eb;  /* blue-700，悬停 */
--color-primary-light:  #eff6ff;  /* blue-50，选中背景 */
--color-primary-ring:   #93c5fd;  /* blue-300，focus ring */

/* 强调渐变（与用户消息气泡一致） */
--gradient-accent: linear-gradient(to right, #a855f7, #6366f1);  /* purple-500 → indigo-500 */

/* 背景 */
--color-bg:             #ffffff;
--color-bg-secondary:   #f9fafb;  /* gray-50 */
--color-bg-hover:       #f3f4f6;  /* gray-100 */

/* 文字 */
--color-text-primary:   #111827;  /* gray-900 */
--color-text-secondary: #6b7280;  /* gray-500 */
--color-text-tertiary:  #9ca3af;  /* gray-400 */

/* 边框 */
--color-border:         #e5e7eb;  /* gray-200 */
--color-border-focus:   #3b82f6;  /* blue-500 */
```

### 状态色

```css
/* 运行中 */
--status-active-bg:     #ecfdf5;  /* green-50 */
--status-active-text:   #059669;  /* green-600 */
--status-active-dot:    #22c55e;  /* green-500，呼吸动画 */

/* 已暂停 */
--status-paused-bg:     #fffbeb;  /* yellow-50 */
--status-paused-text:   #d97706;  /* yellow-600 */
--status-paused-dot:    #eab308;  /* yellow-500 */

/* 失败/错误 */
--status-error-bg:      #fef2f2;  /* red-50 */
--status-error-text:    #dc2626;  /* red-600 */
--status-error-dot:     #ef4444;  /* red-500 */

/* 已停用 */
--status-disabled-bg:   #f9fafb;  /* gray-50 */
--status-disabled-text: #9ca3af;  /* gray-400 */
--status-disabled-dot:  #d1d5db;  /* gray-300 */
```

---

## 三、布局结构

### 3.1 面板位置

任务面板作为**侧边栏 Tab** 集成，与现有文件面板并列：

```
┌──────────────────────────────────────────────────────────┐
│  顶部导航栏                                               │
├──────────┬───────────────────────────────┬───────────────┤
│          │                               │ 💬 聊天       │
│  左侧    │      主聊天区域                │ 📁 文件       │
│  会话    │                               │ ⏰ 任务 ← new │
│  列表    │                               │               │
│          │                               │  [任务面板]    │
│          │                               │               │
└──────────┴───────────────────────────────┴───────────────┘
```

### 3.2 面板内部结构

```
┌─────────────────────────────────────┐
│  ⏰ 定时任务              [+ 新建]  │  ← 面板头部
├─────────────────────────────────────┤
│  ┌─────────────────────────────┐    │
│  │ ✨ 描述你想定时执行的任务...  │    │  ← 自然语言输入
│  └─────────────────────────────┘    │
│                                      │
│  ── 运行中 (2) ──────────────────    │  ← 状态分组标题
│                                      │
│  ┌─────────────────────────────┐    │
│  │ ● 每日销售日报               │    │
│  │   09:00 · 运营群 · 上次 ✅   │    │  ← 任务卡片（折叠态）
│  │                    ▶ ⏸ ⚙    │    │
│  └─────────────────────────────┘    │
│                                      │
│  ┌─────────────────────────────┐    │
│  │ ● 库存预警                   │    │
│  │   08:00 · 仓管群 · 上次 ✅   │    │
│  │                    ▶ ⏸ ⚙    │    │
│  └─────────────────────────────┘    │
│                                      │
│  ── 已暂停 (1) ──────────────────    │
│                                      │
│  ┌─────────────────────────────┐    │
│  │ ○ 周经营报告                  │    │
│  │   周一 09:00 · 老板 · 上次 ✅ │    │
│  │                    ▶ ⏸ ⚙    │    │
│  └─────────────────────────────┘    │
│                                      │
│  ── 执行历史 ────────────────────    │  ← 底部折叠区
│  │ 今天 09:01  每日销售日报 ✅    │    │
│  │ 今天 08:00  库存预警     ✅    │    │
│  │ 昨天 09:02  每日销售日报 ✅    │    │
└─────────────────────────────────────┘
```

---

## 四、组件样式

### 4.1 任务卡片

#### 折叠态（默认，高密度列表）

```
┌────────────────────────────────────────────┐
│  ● 每日销售日报                    📎 ▶ ⚙  │
│  每天 09:00 · 运营群 · 下次: 明天 09:00    │
│  上次: 今天 09:01 ✅ 耗时 12s 消耗 3积分    │
└────────────────────────────────────────────┘
```

**样式规格**：
- 背景: `white`，边框: `1px solid var(--color-border)`
- 圆角: `8px`
- 内边距: `12px 16px`
- 悬停: `background: var(--color-bg-hover)`，`border-color: var(--color-primary-ring)`
- 过渡: `all 150ms cubic-bezier(0.4, 0, 0.2, 1)`
- 状态圆点: `8px` 圆形，带状态色

**状态圆点样式**：
```css
/* 运行中 — 呼吸动画 */
.dot-active {
  width: 8px; height: 8px;
  background: var(--status-active-dot);
  border-radius: 50%;
  animation: breathe 2s ease-in-out infinite;
}

/* 已暂停 — 静态 */
.dot-paused {
  background: var(--status-paused-dot);
}

/* 失败 — 闪烁 */
.dot-error {
  background: var(--status-error-dot);
  animation: blink 1.5s ease-in-out infinite;
}
```

#### 展开态（点击卡片展开详情）

```
┌────────────────────────────────────────────┐
│  ● 每日销售日报                    📎 ▶ ⚙  │
│  每天 09:00 · 运营群 · 下次: 明天 09:00    │
├────────────────────────────────────────────┤
│                                             │
│  任务指令                                    │
│  ┌────────────────────────────────────┐     │
│  │ 查询昨日各店铺销售数据，按销售额    │     │
│  │ 降序生成汇总表格，对比前日标注增降幅 │     │
│  └────────────────────────────────────┘     │
│                                             │
│  模板文件                                    │
│  ┌────────────────────────────────────┐     │
│  │ 📎 销售日报模板.xlsx    [更换] [移除]│     │
│  └────────────────────────────────────┘     │
│                                             │
│  推送目标                                    │
│  ┌────────────────────────────────────┐     │
│  │ 👥 运营群     [更换]                │     │
│  └────────────────────────────────────┘     │
│                                             │
│  执行记录                                    │
│  ├ 04-09 09:01  ✅ 12s  3积分  📄 日报.xlsx │
│  ├ 04-08 09:02  ✅ 15s  3积分  📄 日报.xlsx │
│  ├ 04-07 09:01  ❌ "ERP接口超时"            │
│  └ 04-06 09:03  ✅ 11s  3积分  📄 日报.xlsx │
│                                             │
│  [立即执行]          [暂停任务]    [删除任务] │
└────────────────────────────────────────────┘
```

**展开动画**：
```css
.card-expand {
  animation: expandCard 200ms cubic-bezier(0.4, 0, 0.2, 1);
  transform-origin: top;
}

@keyframes expandCard {
  from { max-height: 80px; opacity: 0.8; }
  to   { max-height: 500px; opacity: 1; }
}
```

### 4.2 状态指示器 Badge

```css
.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 9999px;  /* pill 形 */
  font-size: 12px;
  font-weight: 500;
  gap: 4px;
  transition: all 150ms ease;
}

.badge-active {
  background: var(--status-active-bg);
  color: var(--status-active-text);
}

.badge-paused {
  background: var(--status-paused-bg);
  color: var(--status-paused-text);
}

.badge-error {
  background: var(--status-error-bg);
  color: var(--status-error-text);
}

.badge-success {
  background: var(--status-active-bg);
  color: var(--status-active-text);
}
```

### 4.3 自然语言输入框

```
┌──────────────────────────────────────────────────┐
│ ✨ 描述你想定时执行的任务...                       │
│                                                    │
│   例: "每天早上9点把昨日销售日报发到运营群"          │
└──────────────────────────────────────────────────┘
```

**样式**：
```css
.nl-input {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 12px 16px;
  font-size: 14px;
  color: var(--color-text-primary);
  transition: all 200ms cubic-bezier(0.4, 0, 0.2, 1);
}

.nl-input:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
  background: white;
}

.nl-input::placeholder {
  color: var(--color-text-tertiary);
}
```

### 4.4 创建/编辑表单（Modal 或面板内展开）

Agent 解析自然语言后，展示结构化表单让用户确认：

```
┌──────────────────────────────────────────────┐
│  ✨ AI 已理解你的需求，请确认：                │
├──────────────────────────────────────────────┤
│                                               │
│  任务名称                                      │
│  ┌──────────────────────────────────┐         │
│  │ 每日销售日报                      │         │
│  └──────────────────────────────────┘         │
│                                               │
│  执行时间                                      │
│  ┌──────────────────────────────────┐         │
│  │ 每天  ▾ │  09 : 00              │         │
│  └──────────────────────────────────┘         │
│                                               │
│  任务指令                                      │
│  ┌──────────────────────────────────┐         │
│  │ 查询昨日各店铺销售数据，按销售额  │         │
│  │ 降序生成汇总表格...              │         │
│  └──────────────────────────────────┘         │
│                                               │
│  推送目标                                      │
│  ┌──────────────────────────────────┐         │
│  │ 👥 运营群 (chatid: xxx)     ✕   │         │
│  │ [+ 添加推送目标]                  │         │
│  └──────────────────────────────────┘         │
│                                               │
│  模板文件（可选）                               │
│  ┌──────────────────────────────────┐         │
│  │     📎 点击上传或拖拽模板文件      │         │
│  │     支持 xlsx / csv / json       │         │
│  └──────────────────────────────────┘         │
│                                               │
│  高级设置                              展开 ▾  │
│  │ 单次最大积分:  10                          │
│  │ 失败重试次数:  1                           │
│  │ 执行超时:      180秒                       │
│                                               │
│         [取消]              [创建任务]          │
└──────────────────────────────────────────────┘
```

**表单组件样式**：
```css
/* 输入框 */
.form-input {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 14px;
  transition: border-color 150ms ease, box-shadow 150ms ease;
}

.form-input:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
  outline: none;
}

/* 时间选择器 — 自定义下拉 */
.time-picker {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 8px 12px;
}

/* 频率选择器 — Pill 切换组 */
.frequency-pills {
  display: flex;
  gap: 4px;
  background: var(--color-bg-secondary);
  border-radius: 8px;
  padding: 2px;
}

.frequency-pill {
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 150ms ease;
}

.frequency-pill.active {
  background: white;
  color: var(--color-primary);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}
```

---

## 五、交互动画规范

### 5.1 全局过渡曲线

```css
/* 标准过渡 — 大多数状态变化 */
--ease-standard: cubic-bezier(0.4, 0, 0.2, 1);  /* Material ease-in-out */
--duration-fast: 150ms;     /* 悬停、焦点 */
--duration-normal: 200ms;   /* 展开、收起 */
--duration-slow: 300ms;     /* 面板滑入、模态框 */

/* 弹性过渡 — 创建成功、状态切换 */
--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
--duration-spring: 400ms;

/* 退出过渡 — 删除、关闭 */
--ease-exit: cubic-bezier(0.4, 0, 1, 1);
--duration-exit: 150ms;
```

### 5.2 任务卡片动画

#### 创建（新任务出现）

```css
@keyframes taskSlideIn {
  0% {
    opacity: 0;
    transform: translateY(-8px) scale(0.98);
    max-height: 0;
  }
  60% {
    opacity: 1;
    transform: translateY(2px) scale(1.005);  /* 微弹 */
    max-height: 100px;
  }
  100% {
    transform: translateY(0) scale(1);
    max-height: 100px;
  }
}

.task-enter {
  animation: taskSlideIn 400ms var(--ease-spring);
}
```

#### 展开/收起

```css
.task-details {
  display: grid;
  grid-template-rows: 0fr;           /* 收起 */
  opacity: 0;
  transition: grid-template-rows var(--duration-normal) var(--ease-standard),
              opacity var(--duration-fast) var(--ease-standard);
}

.task-details.expanded {
  grid-template-rows: 1fr;           /* 展开 */
  opacity: 1;
}

.task-details > .inner {
  overflow: hidden;
}
```

#### 删除

```css
@keyframes taskSlideOut {
  0% {
    opacity: 1;
    transform: translateX(0) scale(1);
    max-height: 100px;
  }
  40% {
    opacity: 0.5;
    transform: translateX(16px) scale(0.98);
  }
  100% {
    opacity: 0;
    transform: translateX(32px) scale(0.95);
    max-height: 0;
    margin: 0;
    padding: 0;
  }
}

.task-exit {
  animation: taskSlideOut 300ms var(--ease-exit) forwards;
}
```

#### 状态切换（暂停 ↔ 恢复）

```css
/* 状态圆点颜色过渡 */
.status-dot {
  transition: background-color 300ms var(--ease-standard),
              box-shadow 300ms var(--ease-standard);
}

/* 状态 badge 颜色过渡 */
.status-badge {
  transition: background-color 200ms var(--ease-standard),
              color 200ms var(--ease-standard);
}

/* 暂停时卡片整体降低对比 */
.task-card.paused {
  opacity: 0.65;
  transition: opacity 300ms var(--ease-standard);
}

.task-card.paused:hover {
  opacity: 0.85;
}
```

### 5.3 状态呼吸动画

```css
/* 运行中 — 呼吸光晕（柔和，不刺眼） */
@keyframes breathe {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.4);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(34, 197, 94, 0);
  }
}

/* 失败 — 缓慢闪烁（提醒但不焦虑） */
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* 执行中 — 旋转加载 */
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.task-running .status-icon {
  animation: spin 1.5s linear infinite;
}
```

### 5.4 面板滑入/滑出

```css
/* 右侧面板滑入 */
@keyframes panelSlideIn {
  from {
    transform: translateX(100%);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}

.task-panel-enter {
  animation: panelSlideIn 300ms var(--ease-standard);
}

/* 面板滑出 */
@keyframes panelSlideOut {
  from {
    transform: translateX(0);
    opacity: 1;
  }
  to {
    transform: translateX(100%);
    opacity: 0;
  }
}

.task-panel-exit {
  animation: panelSlideOut 200ms var(--ease-exit);
}
```

### 5.5 按钮微交互

```css
/* 主按钮 */
.btn-primary {
  background: var(--color-primary);
  color: white;
  border-radius: 8px;
  padding: 8px 16px;
  font-size: 14px;
  font-weight: 500;
  transition: all var(--duration-fast) var(--ease-standard);
}

.btn-primary:hover {
  background: var(--color-primary-hover);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.25);
}

.btn-primary:active {
  transform: translateY(0);
  box-shadow: none;
}

/* 危险按钮（删除） */
.btn-danger {
  color: var(--status-error-text);
  background: transparent;
  border: 1px solid var(--color-border);
  transition: all var(--duration-fast) var(--ease-standard);
}

.btn-danger:hover {
  background: var(--status-error-bg);
  border-color: #fecaca;  /* red-200 */
}

/* 图标按钮（播放/暂停/设置） */
.btn-icon {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--duration-fast) var(--ease-standard);
}

.btn-icon:hover {
  background: var(--color-bg-hover);
}
```

### 5.6 模板文件上传交互

```css
/* 拖拽区域 */
.upload-zone {
  border: 2px dashed var(--color-border);
  border-radius: 8px;
  padding: 24px;
  text-align: center;
  transition: all 200ms var(--ease-standard);
}

/* 拖拽悬停态 */
.upload-zone.drag-over {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
  transform: scale(1.01);
}

/* 上传成功 — 文件卡片弹入 */
@keyframes fileCardIn {
  0% {
    opacity: 0;
    transform: scale(0.9);
  }
  70% {
    transform: scale(1.02);
  }
  100% {
    opacity: 1;
    transform: scale(1);
  }
}

.file-card-enter {
  animation: fileCardIn 300ms var(--ease-spring);
}
```

### 5.7 执行历史时间线

```css
/* 历史条目逐条淡入 */
@keyframes historyFadeIn {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.history-item {
  animation: historyFadeIn 200ms var(--ease-standard);
  animation-fill-mode: backwards;
}

/* 交错延迟 */
.history-item:nth-child(1) { animation-delay: 0ms; }
.history-item:nth-child(2) { animation-delay: 50ms; }
.history-item:nth-child(3) { animation-delay: 100ms; }
.history-item:nth-child(4) { animation-delay: 150ms; }
.history-item:nth-child(5) { animation-delay: 200ms; }
```

### 5.8 "立即执行" 反馈

```css
/* 点击立即执行 → 按钮变为进度态 */
.btn-run.running {
  background: var(--color-primary-light);
  color: var(--color-primary);
  pointer-events: none;
}

.btn-run.running::after {
  content: '';
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid var(--color-primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-left: 8px;
}

/* 执行完成 → 弹出成功 */
@keyframes checkmark {
  0% { transform: scale(0); }
  50% { transform: scale(1.2); }
  100% { transform: scale(1); }
}

.run-success-icon {
  animation: checkmark 400ms var(--ease-spring);
  color: var(--status-active-text);
}
```

---

## 六、自然语言创建交互流程

### 6.1 完整流程

```
用户输入: "每天早上9点把昨日销售日报发到运营群"
  │
  ↓ (输入框下方出现加载动画)
  │
  ├─ AI 解析中...  [3个圆点呼吸动画，150ms间隔]
  │
  ↓ (200ms 后表单从输入框下方展开)
  │
  ├─ 预填充表单（AI 理解结果）:
  │   名称: 每日销售日报
  │   时间: 每天 09:00
  │   指令: 查询昨日各店铺销售数据...
  │   目标: 运营群
  │
  ↓ (用户可修改任意字段)
  │
  └─ 点击 [创建任务]
      │
      ├─ 按钮 → loading 态 (150ms)
      ├─ 表单收起 (200ms ease-exit)
      ├─ 新任务卡片从顶部滑入列表 (400ms spring)
      └─ 成功 Toast: "已创建定时任务「每日销售日报」" (自动消失 3s)
```

### 6.2 解析加载动画

```css
/* AI 解析中的圆点动画 */
@keyframes dotPulse {
  0%, 80%, 100% {
    opacity: 0.3;
    transform: scale(0.8);
  }
  40% {
    opacity: 1;
    transform: scale(1);
  }
}

.parsing-dots span {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-primary);
  margin: 0 2px;
  animation: dotPulse 1.2s ease-in-out infinite;
}

.parsing-dots span:nth-child(2) { animation-delay: 150ms; }
.parsing-dots span:nth-child(3) { animation-delay: 300ms; }
```

### 6.3 智能建议（输入过程中）

当用户输入时，下方显示推送目标的自动补全（从 wecom_chat_targets 拉取）：

```
┌─────────────────────────────────────────────┐
│ 每天9点推销售数据到运|                        │
├─────────────────────────────────────────────┤
│  💬 运营群        最近活跃: 今天             │
│  💬 运营交流群    最近活跃: 昨天             │
└─────────────────────────────────────────────┘
```

```css
.autocomplete-dropdown {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: white;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  animation: dropdownFadeIn 150ms var(--ease-standard);
  overflow: hidden;
}

@keyframes dropdownFadeIn {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.autocomplete-item {
  padding: 8px 12px;
  cursor: pointer;
  transition: background var(--duration-fast) ease;
}

.autocomplete-item:hover {
  background: var(--color-bg-hover);
}
```

---

## 七、响应式适配

### 7.1 断点策略

| 断点 | 任务面板行为 |
|------|------------|
| **桌面 ≥1280px** | 固定右侧面板，宽度 320px |
| **小桌面 1024-1279px** | 固定右侧面板，宽度 280px |
| **平板 768-1023px** | 浮动面板（点击 Tab 时覆盖聊天区右侧），宽度 320px |
| **手机 <768px** | 全屏面板（从底部滑入），占满屏幕 |

### 7.2 手机端适配

```
┌───────────────────────┐
│  ← 定时任务    [+ 新建]│
├───────────────────────┤
│                        │
│  ┌──────────────────┐  │
│  │ ✨ 描述任务...    │  │
│  └──────────────────┘  │
│                        │
│  ● 每日销售日报        │
│    09:00 · 运营群      │
│    上次 ✅ · ▶ ⏸ ⚙    │
│  ─────────────────────│
│  ● 库存预警            │
│    08:00 · 仓管群      │
│    上次 ✅ · ▶ ⏸ ⚙    │
│  ─────────────────────│
│                        │
└───────────────────────┘
```

手机端任务卡片简化为**无边框列表**（分割线替代卡片边框），减少视觉负担。

---

## 八、空状态设计

```
┌──────────────────────────────────────┐
│                                       │
│          ⏰                           │
│                                       │
│    还没有定时任务                      │
│                                       │
│    让 AI 帮你自动推送日报、预警、      │
│    周报到企微群                        │
│                                       │
│    ┌──────────────────────────────┐   │
│    │ ✨ 例如: "每天9点推销售日报"  │   │
│    └──────────────────────────────┘   │
│                                       │
│    或 [手动创建]                       │
│                                       │
└──────────────────────────────────────┘
```

```css
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 24px;
  color: var(--color-text-secondary);
  text-align: center;
}

.empty-state-icon {
  font-size: 48px;
  margin-bottom: 16px;
  animation: float 3s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}
```

---

## 九、Toast 通知

```css
/* 成功 Toast */
.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: white;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 12px 16px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  display: flex;
  align-items: center;
  gap: 8px;
  z-index: 50;
}

/* 滑入 */
.toast-enter {
  animation: toastIn 300ms var(--ease-spring);
}

@keyframes toastIn {
  from {
    opacity: 0;
    transform: translateY(16px) scale(0.95);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* 滑出 */
.toast-exit {
  animation: toastOut 200ms var(--ease-exit) forwards;
}

@keyframes toastOut {
  to {
    opacity: 0;
    transform: translateY(8px) scale(0.98);
  }
}
```

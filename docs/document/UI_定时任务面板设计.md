# UI 设计方案：定时任务面板

> 版本：V2.1 | 日期：2026-04-11
> 基于：前端 V3 架构（3 层 token 系统 + cva 组件库 + framer-motion）
> 自动适配：Classic / Claude / Linear 三套主题 + light/dark 模式
> 权限集成：[TECH_组织架构与权限模型.md](./TECH_组织架构与权限模型.md)

---

## 一、设计原则

| 原则 | 实现策略 |
|------|---------|
| **零自定义颜色** | 全部用 `--s-*` 语义 token，自动跟随主题切换 |
| **零自定义组件** | 复用 `ui/Button`、`ui/Card`、`ui/Badge`、`ui/Input`、`ui/Dropdown` |
| **零自定义动画** | 复用 `utils/motion.ts` 的 spring presets 和 `styles/animations.css` 的 keyframes |
| **跟随面板模式** | 与 `SearchPanel` 一致：右侧 drawer 覆盖 + AnimatePresence + FLUID_SPRING |
| **三主题兼容** | Classic / Claude / Linear 自动适配，无需为每套主题单独写样式 |

---

## 二、颜色 Token 映射

**禁止使用自定义颜色变量**，全部从现有 token 引用：

### 2.1 基础颜色（语义层 `--s-*`）

```css
/* 表面 */
bg-surface          → var(--s-surface-base)      /* 页面背景 */
bg-surface-card     → var(--s-surface-raised)    /* 卡片背景 */
bg-surface-overlay  → var(--s-surface-overlay)   /* 模态/抽屉背景 */
bg-surface-sunken   → var(--s-surface-sunken)    /* 嵌入区域 */

/* 文字 */
text-text-primary   → var(--s-text-primary)      /* 主文本 */
text-text-secondary → var(--s-text-secondary)    /* 次文本 */
text-text-tertiary  → var(--s-text-tertiary)     /* 弱化文本 */
text-text-disabled  → var(--s-text-disabled)     /* 禁用 */

/* 强调 */
bg-accent           → var(--s-accent-default)    /* 主色 */
bg-accent-hover     → var(--s-accent-hover)
bg-accent-soft      → var(--s-accent-soft)       /* 选中态浅背景 */

/* 边框 */
border-border-subtle  → var(--s-border-subtle)
border-border-default → var(--s-border-default)
border-border-strong  → var(--s-border-strong)
border-border-focus   → var(--s-border-focus)
```

### 2.2 状态色（语义层）

```css
/* 运行中 */
text-success → var(--s-success)
bg-success-soft → var(--s-success-soft)

/* 失败 */
text-error → var(--s-error)
bg-error-soft → var(--s-error-soft)

/* 暂停 */
text-warning → var(--s-warning)
bg-warning-soft → var(--s-warning-soft)
```

### 2.3 主题适配验证

| 元素 | Classic 蓝色 | Claude 暖色 | Linear 暗色 |
|------|------------|------------|------------|
| 主按钮 | `#2563eb` 蓝 | `#c96442` 赤陶 | `#5e6ad2` 靛蓝 |
| 卡片背景 | `#ffffff` 白 | `#faf9f5` 象牙 | `rgba(255,255,255,0.02)` |
| 主文字 | `#111827` 黑 | `#141413` 暖黑 | `#f7f8f8` 近白 |
| 边框 | `#e5e7eb` 灰 | `#f0eee6` 奶油 | `rgba(255,255,255,0.08)` |

**所有都通过同一套 token 引用自动切换，无需任何主题判断代码。**

---

## 三、组件复用策略

### 3.1 使用现有 ui/ 组件

| 用途 | 现有组件 | 配置 |
|------|---------|------|
| 创建/确认按钮 | `<Button variant="accent" size="md" />` | 主按钮 |
| 暂停/恢复 | `<Button variant="ghost" size="sm" icon={...} />` | 图标按钮 |
| 删除 | `<Button variant="danger" size="sm" />` | 危险按钮 |
| 任务卡片 | `<Card variant="interactive" padding="md" />` | 可交互卡片 |
| 任务详情 | `<Card variant="elevated" padding="lg" />` | 提升卡片 |
| 状态标签 | `<Badge variant="success/error/warning" pulse />` | 内置脉冲 |
| 任务名输入 | `<Input label="任务名" />` | 标准输入 |
| 频率选择 | `<Dropdown />` | Radix 下拉 |

### 3.2 不需要新建任何 UI 组件

只需创建**业务组件**（组合现有 ui/ 组件）：

```
frontend/src/components/scheduled-tasks/
├── ScheduledTaskPanel.tsx       # Drawer 主面板
├── TaskList.tsx                 # 列表
├── TaskCard.tsx                 # 业务卡片（组合 Card + Badge + Button）
├── TaskForm.tsx                 # 业务表单（组合 Input + Dropdown + Button）
├── NaturalLanguageInput.tsx     # 业务输入框（组合 Input + 解析逻辑）
├── PushTargetSelector.tsx       # 业务选择器（组合 Dropdown + 自动补全）
├── TaskRunHistory.tsx           # 业务列表
├── EmptyState.tsx               # 业务空状态
└── hooks/
    ├── useScheduledTasks.ts
    └── useTaskParse.ts
```

---

## 四、布局结构

### 4.1 面板集成方式（跟 SearchPanel 一致）

**右侧 drawer 覆盖式**，不是侧边栏 Tab：

```tsx
// frontend/src/pages/Chat.tsx 内
<PageTransition className="h-screen flex bg-surface">
  <Sidebar ... />
  
  <div className="flex-1 flex flex-col min-w-0">
    <ChatHeader 
      ... 
      onOpenScheduledTasks={() => setTaskPanelOpen(true)}  // 新增
    />
    <MessageArea ... />
    <InputArea ... />
    
    {/* 已有 */}
    <SearchPanel isOpen={searchPanelOpen} onClose={...} />
    
    {/* 新增 */}
    <ScheduledTaskPanel 
      isOpen={taskPanelOpen} 
      onClose={() => setTaskPanelOpen(false)} 
    />
  </div>
</PageTransition>
```

### 4.2 入口位置

在 `ChatHeader` 添加打开按钮（跟搜索按钮并列）：

```tsx
<Button 
  variant="ghost" 
  size="sm" 
  icon={<Clock size={18} />}
  onClick={onOpenScheduledTasks}
  aria-label="定时任务"
/>
```

### 4.3 ScheduledTaskPanel 结构

```tsx
import { motion, AnimatePresence } from 'framer-motion';
import { FLUID_SPRING } from '@/utils/motion';

export function ScheduledTaskPanel({ isOpen, onClose }) {
  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* 背景遮罩 */}
          <motion.div
            className="fixed inset-0 z-30 bg-black/40"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />
          
          {/* 抽屉面板 */}
          <motion.div
            className="fixed right-0 top-0 bottom-0 z-40 w-[420px] 
                       bg-surface-card border-l border-border-default
                       flex flex-col"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={FLUID_SPRING}
          >
            <PanelHeader onClose={onClose} />
            <NaturalLanguageInput />
            <TaskList />
            <TaskRunHistorySection />
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
```

### 4.4 面板内部布局

```
┌─────────────────────────────────────┐
│  ⏰ 定时任务              [×]        │  PanelHeader (sticky top)
├─────────────────────────────────────┤
│                                      │
│  ┌─────────────────────────────┐    │
│  │ ✨ 描述任务...                │    │  NaturalLanguageInput
│  └─────────────────────────────┘    │
│                                      │
│  运行中 (2)                          │  分组标题
│  ┌─────────────────────────────┐    │
│  │ ● 每日销售日报          📎 ⚙ │    │  TaskCard
│  │ 每天 09:00 · 运营群          │    │
│  └─────────────────────────────┘    │
│  ┌─────────────────────────────┐    │
│  │ ● 库存预警              ⚙   │    │
│  │ 每天 08:00 · 仓管群          │    │
│  └─────────────────────────────┘    │
│                                      │
│  已暂停 (1)                          │
│  ┌─────────────────────────────┐    │
│  │ ○ 周经营报告            ⚙   │    │
│  │ 每周一 09:00 · 老板          │    │
│  └─────────────────────────────┘    │
│                                      │
├─────────────────────────────────────┤
│  执行历史                  展开 ▾    │  TaskRunHistorySection
│  04-11 09:01  日报 ✅ 12s  3积分    │
│  04-10 09:02  日报 ✅ 14s  3积分    │
└─────────────────────────────────────┘
```

### 4.5 视图切换器（按职位显示）

不同职位看到的视图切换器不同：

| 职位 | 切换器 | 默认视图 |
|------|-------|---------|
| **老板** | `[全公司] [我的]` | 全公司 |
| **全公司副总** | `[全公司] [我的]` | 全公司 |
| **分管副总** | `[运营一部] [运营二部] [我的]` | 第一个分管部门 |
| **主管** | `[运营一部] [我的]` | 本部门 |
| **副主管 / 员工** | （无切换器） | 仅自己 |

```tsx
// frontend/src/components/scheduled-tasks/ViewSwitcher.tsx
import { useAuthStore } from '@/stores/useAuthStore';

export function ViewSwitcher({ value, onChange }) {
  const { currentMember } = useAuthStore();
  const positionCode = currentMember?.position_code;
  
  // 员工/副主管：不显示
  if (positionCode === 'member' || positionCode === 'deputy') {
    return null;
  }
  
  const views = buildViews(currentMember);
  
  return (
    <div className="flex gap-1 p-1 bg-surface-sunken rounded-lg">
      {views.map(view => (
        <button
          key={view.id}
          onClick={() => onChange(view.id)}
          className={cn(
            "px-3 py-1.5 text-sm font-medium rounded-md transition-all",
            value === view.id
              ? "bg-surface-card text-text-primary shadow-sm"
              : "text-text-secondary hover:text-text-primary"
          )}
        >
          {view.label} ({view.count})
        </button>
      ))}
    </div>
  );
}

// 后端 /api/auth/me 返回的当前成员信息，分管部门已 join 部门表
interface CurrentMember {
  user_id: string;
  position_code: 'boss' | 'vp' | 'manager' | 'deputy' | 'member';
  department_id?: string;
  department_name?: string;
  data_scope: 'all' | 'dept_subtree' | 'self';
  managed_departments?: Array<{ id: string; name: string }>;  // 副总的分管部门
}

function buildViews(member: CurrentMember) {
  const views = [];
  
  if (member.position_code === 'boss' || 
      (member.position_code === 'vp' && member.data_scope === 'all')) {
    views.push({ id: 'all', label: '全公司', count: 0 });
  } else if (member.position_code === 'vp') {
    // 分管副总：每个分管部门一个 tab
    member.managed_departments?.forEach(dept => {
      views.push({ id: `dept:${dept.id}`, label: dept.name, count: 0 });
    });
  } else if (member.position_code === 'manager') {
    views.push({ 
      id: `dept:${member.department_id}`, 
      label: member.department_name ?? '本部门', 
      count: 0 
    });
  }
  
  // 所有人都有"我的"
  views.push({ id: 'mine', label: '我的', count: 0 });
  
  return views;
}
```

### 4.6 创建者徽标

老板/副总/主管视角下，每张任务卡片显示**创建者头像 + 部门徽标 + 职位徽标**：

```tsx
// 任务卡片底部新增
{showCreatorBadge && (
  <div className="flex items-center gap-1.5 mt-2 text-xs">
    <Avatar size="xs" name={task.creator.name} />
    <span className="text-text-secondary">{task.creator.name}</span>
    
    <DepartmentBadge type={task.creator.department_type} />
    <PositionBadge level={task.creator.position_code} />
  </div>
)}
```

**徽标颜色映射**：

```ts
// 部门徽标颜色
export const DEPT_COLORS = {
  ops:       { bg: '#dbeafe', text: '#1e40af', label: '运营' },  // 蓝
  finance:   { bg: '#d1fae5', text: '#065f46', label: '财务' },  // 绿
  warehouse: { bg: '#fed7aa', text: '#9a3412', label: '仓库' },  // 橙
  service:   { bg: '#e9d5ff', text: '#6b21a8', label: '客服' },  // 紫
  design:    { bg: '#fce7f3', text: '#9f1239', label: '设计' },  // 粉
  hr:        { bg: '#cffafe', text: '#155e75', label: '人事' },  // 青
};

// 职位徽标颜色
export const POSITION_COLORS = {
  boss:    { bg: '#fef3c7', text: '#b45309', label: '老板' },    // 金
  vp:      { bg: '#f3f4f6', text: '#374151', label: '副总' },    // 银
  manager: { bg: '#dbeafe', text: '#1e3a8a', label: '主管' },    // 深蓝
  deputy:  { bg: '#dbeafe', text: '#60a5fa', label: '副主管' },  // 浅蓝
  member:  { bg: '#f3f4f6', text: '#6b7280', label: '员工' },    // 灰
};
```

### 4.7 操作权限隐藏

按钮根据权限自动显示/隐藏：

```tsx
import { usePermission } from '@/hooks/usePermission';

function TaskCardActions({ task }) {
  const canEdit = usePermission('task.edit', task);
  const canDelete = usePermission('task.delete', task);
  const canExecute = usePermission('task.execute', task);
  
  return (
    <div className="flex gap-1">
      {canEdit && (
        <Button variant="ghost" size="sm" icon={<Settings />} ... />
      )}
      {canExecute && (
        <Button variant="ghost" size="sm" icon={<Play />} ... />
      )}
      {canDelete && (
        <Button variant="ghost" size="sm" icon={<Trash />} ... />
      )}
    </div>
  );
}
```

#### usePermission hook 实现（需新建）

`usePermission` 完全在前端运行，**不调任何后端接口** — 数据来自 `/api/auth/me` 一次性返回的 `current_org.permissions` 和 `current_org.member`：

```tsx
// frontend/src/hooks/usePermission.ts
import { useAuthStore } from '@/stores/useAuthStore';
import type { ScheduledTask } from '@/types/scheduledTask';

/**
 * 检查当前用户对某资源的权限（前端纯逻辑）
 * 
 * 数据源：useAuthStore().user.current_org.permissions + .member
 * 服务端会再校验一次（防绕过），前端只用于 UI 显示控制
 */
export function usePermission(
  permissionCode: string,
  resource?: ScheduledTask | { user_id: string; creator?: { department_id?: string } }
): boolean {
  const user = useAuthStore(s => s.user);
  const currentOrg = user?.current_org;
  
  if (!currentOrg || !user) return false;
  
  // 1. 检查是否拥有该功能权限
  if (!currentOrg.permissions.includes(permissionCode)) {
    return false;
  }
  
  // 2. 没有 resource 参数 → 列表查询，由后端 SQL 注入处理
  if (!resource) return true;
  
  // 3. 检查数据范围
  const member = currentOrg.member;
  
  // 老板 + 全公司副总：全部允许
  if (member.position_code === 'boss' || 
      (member.position_code === 'vp' && member.data_scope === 'all')) {
    return true;
  }
  
  // 分管副总：检查资源创建者是否在分管部门
  if (member.position_code === 'vp' && member.managed_departments) {
    const creatorDeptId = resource.creator?.department_id;
    return member.managed_departments.some(d => d.id === creatorDeptId);
  }
  
  // 主管：本部门所有人
  if (member.position_code === 'manager') {
    const creatorDeptId = resource.creator?.department_id;
    return creatorDeptId === member.department_id;
  }
  
  // 副主管/员工：只能操作自己创建的资源
  return resource.user_id === user.id;
}

/**
 * 立即执行权限：员工/副主管不能强制执行别人的任务
 */
export function useCanExecute(resource?: { user_id: string }): boolean {
  const user = useAuthStore(s => s.user);
  const member = user?.current_org?.member;
  
  if (!member) return false;
  
  // 老板/副总/主管：可以执行（受 usePermission 数据范围限制）
  if (['boss', 'vp', 'manager'].includes(member.position_code)) {
    return usePermission('task.execute', resource);
  }
  
  // 员工/副主管：只能执行自己的
  return resource?.user_id === user?.id;
}
```

#### 数据来源说明

`current_org.member` 和 `current_org.permissions` 由后端 `/api/auth/me` 端点返回，详见 [TECH_组织架构与权限模型.md 第七.五节](./TECH_组织架构与权限模型.md)。

```typescript
// /api/auth/me 返回示例
{
  id: "user_123",
  nickname: "张三",
  current_org: {
    id: "org_456",
    name: "蓝创科技",
    role: "member",
    member: {
      position_code: "manager",
      department_id: "dept_001",
      department_name: "运营一部",
      department_type: "ops",
      data_scope: "dept_subtree",
      managed_departments: null,  // 主管不需要这个字段
    },
    permissions: [
      "task.view", "task.create", "task.edit", "task.delete", "task.execute",
      "order.view", "order.edit", "order.export",
      "product.view", "product.edit",
    ]
  },
  orgs: [{ id: "org_456", name: "蓝创科技", role: "member" }]
}
```

#### 前端 vs 后端权限校验

| 用途 | 实现位置 | 备注 |
|------|---------|------|
| **UI 显示控制**（按钮显示/隐藏）| 前端 `usePermission` | 纯逻辑，无网络请求 |
| **真实权限校验** | 后端 `check_permission` + `apply_data_scope` | 防绕过，最终拦截器 |

**关键原则**：前端 hook 只是 UX 优化（不显示无权限的按钮），**所有写操作和数据查询都必须经过后端权限校验**。前端绕过 hook 直接构造请求，后端会拦截。

---

## 五、组件实现示例

### 5.1 TaskCard

```tsx
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Pause, Play, Settings, Paperclip } from 'lucide-react';
import { motion } from 'framer-motion';
import { SOFT_SPRING } from '@/utils/motion';

interface TaskCardProps {
  task: ScheduledTask;
  onToggle: (id: string) => void;
  onEdit: (id: string) => void;
}

export function TaskCard({ task, onToggle, onEdit }: TaskCardProps) {
  const isRunning = task.status === 'active';
  const isPaused = task.status === 'paused';
  const isError = task.status === 'error';
  
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 32, scale: 0.95 }}
      transition={SOFT_SPRING}
      className={isPaused ? 'opacity-65' : ''}
    >
      <Card variant="interactive" padding="md" className="group">
        <div className="flex items-start justify-between gap-3">
          {/* 状态点 + 任务信息 */}
          <div className="flex items-start gap-2 min-w-0 flex-1">
            <StatusDot status={task.status} />
            
            <div className="min-w-0 flex-1">
              <h4 className="text-sm font-medium text-text-primary truncate">
                {task.name}
              </h4>
              <p className="text-xs text-text-tertiary mt-0.5 truncate">
                {task.cron_readable} · {task.push_target.chat_name || '推送目标'}
              </p>
              {task.last_run_at && (
                <p className="text-xs text-text-tertiary mt-1">
                  上次: {formatRelative(task.last_run_at)}
                  {task.last_result?.status === 'success' && (
                    <Badge variant="success" className="ml-1.5">✓</Badge>
                  )}
                  {isError && (
                    <Badge variant="error" pulse className="ml-1.5">失败</Badge>
                  )}
                </p>
              )}
            </div>
          </div>
          
          {/* 操作按钮（hover 显示） */}
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {task.template_file && (
              <span className="text-text-tertiary" title={task.template_file.name}>
                <Paperclip size={14} />
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              icon={isRunning ? <Pause size={14} /> : <Play size={14} />}
              onClick={() => onToggle(task.id)}
              aria-label={isRunning ? '暂停' : '恢复'}
            />
            <Button
              variant="ghost"
              size="sm"
              icon={<Settings size={14} />}
              onClick={() => onEdit(task.id)}
              aria-label="编辑"
            />
          </div>
        </div>
      </Card>
    </motion.div>
  );
}

function StatusDot({ status }: { status: TaskStatus }) {
  const colorMap = {
    active: 'bg-success',
    paused: 'bg-warning',
    error: 'bg-error',
    running: 'bg-accent',
  };
  
  return (
    <div className="relative mt-1.5 flex-shrink-0">
      <div className={`w-2 h-2 rounded-full ${colorMap[status]}`} />
      {status === 'active' && (
        <div className={`absolute inset-0 rounded-full ${colorMap[status]} animate-breathe`} />
      )}
    </div>
  );
}
```

### 5.2 NaturalLanguageInput

```tsx
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { Sparkles } from 'lucide-react';
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useTaskParse } from './hooks/useTaskParse';

export function NaturalLanguageInput({ onParsed }) {
  const [text, setText] = useState('');
  const { parse, parsing } = useTaskParse();
  
  const handleParse = async () => {
    if (!text.trim()) return;
    const result = await parse(text);
    if (result) {
      onParsed(result);
      setText('');
    }
  };
  
  return (
    <div className="p-4 border-b border-border-subtle">
      <div className="relative">
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="✨ 描述你想定时执行的任务..."
          icon={<Sparkles size={16} className="text-accent" />}
          fullWidth
          onKeyDown={(e) => e.key === 'Enter' && handleParse()}
        />
        
        <AnimatePresence>
          {parsing && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute right-3 top-1/2 -translate-y-1/2"
            >
              <ParsingDots />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      
      <p className="text-xs text-text-tertiary mt-2">
        例: "每天9点把昨日销售日报发到运营群"
      </p>
    </div>
  );
}

function ParsingDots() {
  return (
    <div className="flex gap-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1 h-1 rounded-full bg-accent animate-dot-pulse"
          style={{ animationDelay: `${i * 150}ms` }}
        />
      ))}
    </div>
  );
}
```

### 5.3 TaskList（按状态分组）

```tsx
import { AnimatePresence } from 'framer-motion';

export function TaskList({ tasks, onToggle, onEdit }) {
  const grouped = {
    active: tasks.filter(t => t.status === 'active'),
    paused: tasks.filter(t => t.status === 'paused'),
    error: tasks.filter(t => t.status === 'error'),
  };
  
  if (tasks.length === 0) return <EmptyState />;
  
  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
      {grouped.active.length > 0 && (
        <Section title="运行中" count={grouped.active.length}>
          <AnimatePresence>
            {grouped.active.map(t => (
              <TaskCard key={t.id} task={t} onToggle={onToggle} onEdit={onEdit} />
            ))}
          </AnimatePresence>
        </Section>
      )}
      {grouped.paused.length > 0 && (
        <Section title="已暂停" count={grouped.paused.length}>
          <AnimatePresence>
            {grouped.paused.map(t => (
              <TaskCard key={t.id} task={t} onToggle={onToggle} onEdit={onEdit} />
            ))}
          </AnimatePresence>
        </Section>
      )}
      {grouped.error.length > 0 && (
        <Section title="失败" count={grouped.error.length}>
          <AnimatePresence>
            {grouped.error.map(t => (
              <TaskCard key={t.id} task={t} onToggle={onToggle} onEdit={onEdit} />
            ))}
          </AnimatePresence>
        </Section>
      )}
    </div>
  );
}

function Section({ title, count, children }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-2 px-1">
        <span className="text-xs font-medium text-text-tertiary uppercase tracking-wider">
          {title}
        </span>
        <span className="text-xs text-text-tertiary">({count})</span>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}
```

---

## 六、动画系统（复用现有）

### 6.1 复用 motion.ts 的 Spring Presets

| 场景 | Preset | 用途 |
|------|--------|------|
| 任务卡片创建/切换 | `SOFT_SPRING` | layout 动画、状态切换 |
| 面板滑入/滑出 | `FLUID_SPRING` | 抽屉打开关闭 |
| 按钮点击反馈 | `SNAPPY_SPRING` | hover/tap 微交互 |
| 创建成功弹入 | `BOUNCY_SPRING` | 强反馈场景 |

### 6.2 复用 animations.css 的 Keyframes

| 动画 | 类名 | 用途 |
|------|------|------|
| 状态点呼吸 | `animate-breathe` | 运行中状态指示 |
| 解析加载 | `animate-dot-pulse` | NL 输入解析中 |
| 任务出现 | `animate-message-in` | 列表新增 |
| 任务消失 | `animate-message-out` | 列表删除 |
| 抽屉滑入 | `animate-drawer-enter` | 面板打开（备选 framer） |
| 历史淡入 | `animate-fade-in` | 历史记录加载 |
| Toast 出现 | 已配置 react-hot-toast | 操作反馈 |

### 6.3 不再自定义任何 keyframe

之前 V1 文档里写的 `taskSlideIn` / `taskSlideOut` / `panelSlideIn` 等全部**作废**，使用 framer-motion + 现有 keyframes 替代。

---

## 七、状态管理

### 7.1 复用 Zustand 模式

新建 `useScheduledTaskStore.ts`，跟现有 store 模式一致：

```tsx
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface ScheduledTaskStore {
  tasks: ScheduledTask[];
  loading: boolean;
  
  // CRUD
  fetchTasks: () => Promise<void>;
  createTask: (data: CreateTaskDto) => Promise<ScheduledTask>;
  updateTask: (id: string, data: Partial<ScheduledTask>) => Promise<void>;
  deleteTask: (id: string) => Promise<void>;
  toggleTask: (id: string) => Promise<void>;
  
  // 乐观更新
  optimisticAdd: (task: ScheduledTask) => void;
  optimisticRemove: (id: string) => void;
  optimisticUpdate: (id: string, data: Partial<ScheduledTask>) => void;
}

export const useScheduledTaskStore = create<ScheduledTaskStore>()(
  persist(
    (set, get) => ({
      tasks: [],
      loading: false,
      
      fetchTasks: async () => { /* ... */ },
      createTask: async (data) => { /* ... */ },
      // ...
    }),
    { name: 'everydayai_scheduled_tasks' }
  )
);
```

### 7.2 WebSocket 实时更新

复用现有 `WebSocketContext` + `wsMessageHandlers`：

```tsx
// frontend/src/contexts/wsMessageHandlers.ts 新增
case 'scheduled_task_started':
  useScheduledTaskStore.getState().optimisticUpdate(data.task_id, { status: 'running' });
  break;

case 'scheduled_task_completed':
  useScheduledTaskStore.getState().optimisticUpdate(data.task_id, {
    status: 'active',
    last_run_at: data.finished_at,
    last_result: data.result,
  });
  toast.success(`任务「${data.name}」执行完成`);
  break;

case 'scheduled_task_failed':
  useScheduledTaskStore.getState().optimisticUpdate(data.task_id, {
    status: data.new_status,
    consecutive_failures: data.consecutive_failures,
  });
  toast.error(`任务「${data.name}」执行失败: ${data.error}`);
  break;
```

---

## 八、TypeScript 类型定义

新建 `frontend/src/types/scheduledTask.ts`：

```ts
export type TaskStatus = 'active' | 'paused' | 'error' | 'running';

export interface PushTarget {
  type: 'wecom_group' | 'wecom_user' | 'web' | 'multi';
  chatid?: string;
  chat_name?: string;
  wecom_userid?: string;
  name?: string;
  conversation_id?: string;
  targets?: PushTarget[];
}

export interface TemplateFile {
  path: string;
  name: string;
  url: string;
}

export interface ScheduledTaskCreator {
  name: string;
  avatar?: string;
  department_id?: string;
  department_name?: string;
  department_type?: 'ops' | 'finance' | 'warehouse' | 'service' | 'design' | 'hr';
  position_code?: 'boss' | 'vp' | 'manager' | 'deputy' | 'member';
}

export interface ScheduledTask {
  id: string;
  org_id: string;
  user_id: string;                  // 创建者 user_id
  creator?: ScheduledTaskCreator;   // 后端 join 返回的创建者展示信息
  
  name: string;
  prompt: string;
  cron_expr: string;
  cron_readable: string;
  timezone: string;
  
  push_target: PushTarget;
  template_file?: TemplateFile;
  
  status: TaskStatus;
  max_credits: number;
  retry_count: number;
  timeout_sec: number;
  
  last_summary?: string;
  last_result?: TaskRunResult;
  
  next_run_at?: string;
  last_run_at?: string;
  run_count: number;
  consecutive_failures: number;
  
  created_at: string;
  updated_at: string;
}

export interface TaskRunResult {
  status: 'success' | 'failed';
  tokens?: number;
  duration_ms?: number;
  files?: Array<{ url: string; name: string }>;
}

export interface TaskRun {
  id: string;
  task_id: string;
  status: 'running' | 'success' | 'failed' | 'timeout';
  started_at: string;
  finished_at?: string;
  duration_ms?: number;
  result_summary?: string;
  result_files?: Array<{ url: string; name: string }>;
  push_status?: 'pushed' | 'push_failed' | 'skipped';
  error_message?: string;
  credits_used: number;
  tokens_used: number;
}
```

---

## 九、响应式适配

跟随现有 Tailwind v4 断点：

| 断点 | 行为 |
|------|------|
| `md` (768px+) | Drawer 宽度 420px |
| `<md` | Drawer 全屏覆盖（`w-full`） |

```tsx
className="fixed right-0 top-0 bottom-0 z-40 
           w-full md:w-[420px]
           bg-surface-card border-l border-border-default"
```

---

## 十、可访问性

- 所有按钮带 `aria-label`
- Drawer 打开时锁定背景滚动
- 键盘操作：Esc 关闭面板、Tab 焦点循环
- 复用 Radix Dialog primitive 包装 Drawer（已有）
- 状态色不依赖单一颜色，配合图标和文字
- `prefers-reduced-motion` 自动禁用动画（已在 animations.css 全局处理）

---

## 十一、文件清单

### 新增文件

```
frontend/src/components/scheduled-tasks/
├── ScheduledTaskPanel.tsx       (~120 行)
├── PanelHeader.tsx              (~30 行)
├── TaskList.tsx                 (~80 行)
├── TaskCard.tsx                 (~120 行)
├── TaskForm.tsx                 (~200 行)
├── NaturalLanguageInput.tsx     (~80 行)
├── PushTargetSelector.tsx       (~90 行)
├── TemplateFileUploader.tsx     (~70 行)
├── TaskRunHistory.tsx           (~80 行)
├── EmptyState.tsx               (~40 行)
└── hooks/
    ├── useScheduledTasks.ts     (~60 行)
    └── useTaskParse.ts          (~40 行)

frontend/src/stores/
└── useScheduledTaskStore.ts     (~150 行)

frontend/src/types/
└── scheduledTask.ts             (~80 行)

frontend/src/services/
└── scheduledTaskService.ts      (~120 行)
```

### 修改文件

```
frontend/src/pages/Chat.tsx                    +5 行  (集成面板)
frontend/src/components/chat/layout/ChatHeader.tsx  +5 行  (新增按钮)
frontend/src/contexts/wsMessageHandlers.ts     +20 行 (WS 事件)
```

**总计**：新增 ~1360 行，修改 ~30 行

---

## 十二、与 V1 文档的差异

| 项目 | V1 (旧) | V2 (新) |
|------|--------|--------|
| 颜色 | 自定义 `--color-*` 变量 | 引用 `--s-*` / `--c-*` token |
| 主题 | 仅蓝色 | Classic/Claude/Linear 自动适配 |
| 组件 | 自己写 | 复用 ui/ 组件 |
| 动画 | 自定义 keyframes | 复用 motion.ts + animations.css |
| 面板模式 | 侧边栏 Tab | 右侧 Drawer 覆盖（同 SearchPanel） |
| 状态管理 | 未指定 | Zustand store + slices 模式 |
| TypeScript | 未指定 | 完整类型定义 + cva variants |

---

## 十三、实施清单

> 前置依赖：[TECH_组织架构与权限模型.md](./TECH_组织架构与权限模型.md) Phase 1 完成（成员管理面板 + PermissionChecker V1）

- [ ] Phase 1：类型 + Store + Service（含 creator 字段）
- [ ] Phase 2：基础组件（Panel + List + Card + ViewSwitcher）
- [ ] Phase 3：表单 + 自然语言输入
- [ ] Phase 4：模板上传 + 推送目标选择
- [ ] Phase 5：执行历史 + WebSocket 集成
- [ ] Phase 6：创建者徽标 + 操作权限隐藏（usePermission hook）
- [ ] Phase 7：集成到 ChatHeader + Chat.tsx
- [ ] Phase 8：三主题视觉验证（Classic/Claude/Linear）+ 权限场景测试（5 个职位）

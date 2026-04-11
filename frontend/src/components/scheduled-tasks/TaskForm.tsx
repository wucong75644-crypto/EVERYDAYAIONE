/**
 * TaskForm — 创建/编辑定时任务表单（V2 重设计）
 *
 * 改动 vs V1：
 * - 删除 4 个固定 cron 预设按钮
 * - 频率类型单选：单次 / 每天 / 每周 / 每月 / 自定义 cron
 * - 时间用 HTML5 <input type="time"> 分钟级
 * - 单次任务用 <input type="datetime-local">
 * - 每周 7 个 checkbox 多选（周日-周六）
 * - 每月下拉选 1-31 日
 * - 推送目标三板块：自己 / 同事 / 群聊（普通员工只看到第一个）
 * - AI 智能创建调用 LLM 自动填好 schedule_type/time_str/weekdays/run_at
 *
 * 权限：
 * - 普通员工（无 task.push_to_others）只能选"推送给我自己"
 * - 管理职位（boss/vp/manager/deputy）可选三个板块全部
 */
import { useState, useEffect } from 'react';
import { ArrowLeft, Sparkles, Loader2, User, Users, MessageSquare } from 'lucide-react';
import { Input } from '../ui/Input';
import { Button } from '../ui/Button';
import { useScheduledTaskStore } from '../../stores/useScheduledTaskStore';
import { scheduledTaskService } from '../../services/scheduledTask';
import { orgMembersService } from '../../services/orgMembers';
import { wecomChatTargetsService } from '../../services/wecomChatTargets';
import { usePermission } from '../../hooks/usePermission';
import { useAuthStore } from '../../stores/useAuthStore';
import { logger } from '../../utils/logger';
import { cn } from '../../utils/cn';
import type {
  ScheduledTask,
  CreateTaskDto,
  ScheduleType,
  PushTarget,
} from '../../types/scheduledTask';
import type { WecomCollectedMember } from '../../types/orgMembers';
import type { WecomGroup } from '../../types/wecomChatTargets';

interface Props {
  task: ScheduledTask | null;  // null = 新建
  onClose: () => void;
  onSaved: () => void;
}

const WEEKDAY_LABELS: { value: number; label: string }[] = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 0, label: '日' },
];

type PushTargetMode = 'self' | 'colleague' | 'group';

/** 把 ISO 时间字符串转成 datetime-local 输入框需要的本地时间格式 */
function isoToLocalDatetime(iso: string | null | undefined): string {
  if (!iso) {
    // 默认值：今天 + 1 小时
    const now = new Date();
    now.setHours(now.getHours() + 1);
    now.setMinutes(0, 0, 0);
    return formatLocalDatetime(now);
  }
  const d = new Date(iso);
  return formatLocalDatetime(d);
}

function formatLocalDatetime(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
}

/** datetime-local 字符串 → ISO 带本地时区偏移 */
function localDatetimeToIso(local: string): string {
  // local 是 "YYYY-MM-DDTHH:MM"，浏览器解析为本地时间
  const d = new Date(local);
  return d.toISOString();
}

export function TaskForm({ task, onClose, onSaved }: Props) {
  const isEdit = task !== null;
  const createTask = useScheduledTaskStore((s) => s.createTask);
  const updateTask = useScheduledTaskStore((s) => s.updateTask);
  const currentUserId = useAuthStore((s) => s.user?.id) || '';

  const canPushToOthers = usePermission('task.push_to_others');

  // ─── 表单字段 ───
  const [name, setName] = useState(task?.name || '');
  const [prompt, setPrompt] = useState(task?.prompt || '');

  // 频率
  const [scheduleType, setScheduleType] = useState<ScheduleType>(
    task?.schedule_type || 'daily',
  );
  const [timeStr, setTimeStr] = useState<string>(() => {
    if (task?.cron_expr) {
      // 从 cron 还原 HH:MM
      const parts = task.cron_expr.split(' ');
      if (parts.length === 5) {
        return `${parts[1].padStart(2, '0')}:${parts[0].padStart(2, '0')}`;
      }
    }
    return '09:00';
  });
  const [weekdays, setWeekdays] = useState<number[]>(task?.weekdays || [1]);
  const [dayOfMonth, setDayOfMonth] = useState<number>(task?.day_of_month || 1);
  const [runAtLocal, setRunAtLocal] = useState<string>(
    isoToLocalDatetime(task?.run_at),
  );
  const [customCron, setCustomCron] = useState<string>(
    task?.schedule_type === 'cron' && task?.cron_expr ? task.cron_expr : '0 9 * * *',
  );

  // 推送目标
  const [pushMode, setPushMode] = useState<PushTargetMode>(() => {
    const t = task?.push_target;
    if (!t) return 'self';
    if (t.type === 'wecom_group') return 'group';
    if (t.type === 'wecom_user') {
      // 看是不是自己
      return t.wecom_userid && currentUserId ? 'self' : 'colleague';
    }
    return 'self';
  });
  const [colleagueId, setColleagueId] = useState<string>(
    task?.push_target?.type === 'wecom_user'
      ? task.push_target.wecom_userid || ''
      : '',
  );
  const [groupId, setGroupId] = useState<string>(
    task?.push_target?.type === 'wecom_group'
      ? task.push_target.chatid || ''
      : '',
  );

  const [colleagues, setColleagues] = useState<WecomCollectedMember[]>([]);
  const [groups, setGroups] = useState<WecomGroup[]>([]);
  const [myWecomUserid, setMyWecomUserid] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 自然语言输入
  const [nlText, setNlText] = useState('');
  const [parsing, setParsing] = useState(false);

  // 任何用户都需要自己的 wecom_userid（"推送给我自己"模式构造 push_target）
  // 用 /org-members/me 接口（任何成员都能调，不需要管理员权限）
  useEffect(() => {
    let cancelled = false;
    orgMembersService
      .getMyMemberInfo()
      .then((info) => {
        if (cancelled) return;
        if (info.wecom_userid) setMyWecomUserid(info.wecom_userid);
      })
      .catch((err) => logger.warn('task-form', `拉取 me 失败: ${err}`));
    return () => { cancelled = true; };
  }, []);

  // 管理员额外加载：员工列表 + 群列表（用于"推给同事/群"两个板块）
  useEffect(() => {
    if (!canPushToOthers) return;
    let cancelled = false;
    Promise.all([
      orgMembersService.listWecomCollected().catch(() => []),
      wecomChatTargetsService.listGroups().catch(() => []),
    ])
      .then(([members, grps]) => {
        if (cancelled) return;
        setColleagues(members);
        setGroups(grps);
      })
      .catch((err) => logger.error('task-form', '拉取员工/群失败', err));
    return () => { cancelled = true; };
  }, [canPushToOthers]);

  // 编辑模式：等 myWecomUserid 加载完后校正 pushMode
  // 否则"推送给同事"的任务会被误判为"推送给自己"
  useEffect(() => {
    if (!task || !myWecomUserid) return;
    const t = task.push_target;
    if (t?.type === 'wecom_user') {
      setPushMode(t.wecom_userid === myWecomUserid ? 'self' : 'colleague');
    }
  }, [task, myWecomUserid]);

  const handleNLParse = async () => {
    if (!nlText.trim()) return;
    setParsing(true);
    try {
      const result = await scheduledTaskService.parseNL(nlText);
      if (result.name) setName(result.name);
      if (result.prompt) setPrompt(result.prompt);
      if (result.schedule_type) setScheduleType(result.schedule_type);
      if (result.time_str) setTimeStr(result.time_str);
      if (result.weekdays) setWeekdays(result.weekdays);
      if (result.day_of_month) setDayOfMonth(result.day_of_month);
      if (result.run_at) setRunAtLocal(isoToLocalDatetime(result.run_at));
      setNlText('');
    } catch (err) {
      logger.error('task-form', 'parse failed', err);
    } finally {
      setParsing(false);
    }
  };

  const toggleWeekday = (day: number) => {
    setWeekdays((prev) =>
      prev.includes(day)
        ? prev.filter((d) => d !== day)
        : [...prev, day].sort((a, b) => a - b),
    );
  };

  const buildPushTarget = (): PushTarget | null => {
    if (pushMode === 'self') {
      if (myWecomUserid) {
        return { type: 'wecom_user', wecom_userid: myWecomUserid };
      }
      // 没绑定企微的散客 → 用 web 模式
      return { type: 'web', user_id: currentUserId };
    }
    if (pushMode === 'colleague') {
      if (!colleagueId) return null;
      const c = colleagues.find((x) => x.wecom_userid === colleagueId);
      return {
        type: 'wecom_user',
        wecom_userid: colleagueId,
        name: c?.nickname,
      };
    }
    if (pushMode === 'group') {
      if (!groupId) return null;
      const g = groups.find((x) => x.id === groupId);
      return {
        type: 'wecom_group',
        chatid: g?.chatid || '',
        chat_name: g?.chat_name || undefined,
      };
    }
    return null;
  };

  const handleSubmit = async () => {
    setError(null);

    if (!name.trim() || !prompt.trim()) {
      setError('请填写任务名称和指令');
      return;
    }

    const target = buildPushTarget();
    if (!target) {
      setError('请选择推送目标');
      return;
    }

    // 校验频率
    if (scheduleType === 'weekly' && weekdays.length === 0) {
      setError('请至少选择一天');
      return;
    }
    if (scheduleType === 'cron' && !customCron.trim()) {
      setError('请填写 cron 表达式');
      return;
    }

    const dto: CreateTaskDto = {
      name: name.trim(),
      prompt: prompt.trim(),
      schedule_type: scheduleType,
      push_target: target,
    };

    if (scheduleType === 'once') {
      dto.run_at = localDatetimeToIso(runAtLocal);
    } else if (scheduleType === 'cron') {
      dto.cron_expr = customCron.trim();
    } else {
      dto.time_str = timeStr;
      if (scheduleType === 'weekly') dto.weekdays = weekdays;
      if (scheduleType === 'monthly') dto.day_of_month = dayOfMonth;
    }

    setSubmitting(true);
    try {
      if (isEdit && task) {
        const ok = await updateTask(task.id, dto);
        if (ok) onSaved();
        else setError('更新失败');
      } else {
        const created = await createTask(dto);
        if (created) onSaved();
        else setError('创建失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {/* 头部 */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--s-border-default)]">
        <button
          type="button"
          onClick={onClose}
          aria-label="返回"
          className="p-1 rounded text-[var(--s-text-tertiary)] hover:bg-[var(--s-hover)] hover:text-[var(--s-text-primary)] transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <h2 className="text-sm font-medium text-[var(--s-text-primary)]">
          {isEdit ? '编辑定时任务' : '新建定时任务'}
        </h2>
      </div>

      {/* 表单内容 */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {/* AI 智能创建（仅新建时） */}
        {!isEdit && (
          <div className="bg-[var(--s-surface-sunken)] rounded-lg p-3">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="w-4 h-4 text-[var(--s-accent)]" />
              <span className="text-xs font-medium text-[var(--s-text-secondary)]">
                AI 智能创建（一句话生成任务）
              </span>
            </div>
            <div className="flex gap-2">
              <Input
                value={nlText}
                onChange={(e) => setNlText(e.target.value)}
                placeholder="如：今晚10点推今日付款订单情况 / 每天9点推销售日报"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleNLParse();
                  }
                }}
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={handleNLParse}
                disabled={parsing || !nlText.trim()}
              >
                {parsing ? <Loader2 className="w-4 h-4 animate-spin" /> : '解析'}
              </Button>
            </div>
            <p className="text-[10px] text-[var(--s-text-tertiary)] mt-1.5">
              解析后会自动填好下面所有字段，可手动微调
            </p>
          </div>
        )}

        <Input
          label="任务名称 *"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="如: 每日销售日报"
        />

        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5">
            任务指令 *
          </label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="如: 查询昨日各店铺销售数据，按销售额降序生成汇总"
            rows={3}
            className={cn(
              'w-full px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
              'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
              'border border-[var(--c-input-border)]',
              'placeholder:text-[var(--c-input-placeholder)]',
              'focus:outline-none focus:border-[var(--c-input-border-focus)]',
              'focus:shadow-[var(--c-input-ring-focus)]',
            )}
          />
        </div>

        {/* ── 执行频率 ── */}
        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-2">
            执行频率 *
          </label>
          <div className="grid grid-cols-5 gap-2 mb-3">
            {(['once', 'daily', 'weekly', 'monthly', 'cron'] as ScheduleType[]).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => setScheduleType(type)}
                className={cn(
                  'px-2 py-1.5 text-xs font-medium rounded-md border transition-colors',
                  scheduleType === type
                    ? 'bg-[var(--s-accent-soft)] text-[var(--s-accent)] border-[var(--s-accent)]'
                    : 'border-[var(--s-border-default)] text-[var(--s-text-secondary)] hover:bg-[var(--s-hover)]',
                )}
              >
                {{
                  once: '单次',
                  daily: '每天',
                  weekly: '每周',
                  monthly: '每月',
                  cron: '高级',
                }[type]}
              </button>
            ))}
          </div>

          {/* 单次：日期+时间 */}
          {scheduleType === 'once' && (
            <input
              type="datetime-local"
              value={runAtLocal}
              onChange={(e) => setRunAtLocal(e.target.value)}
              className={cn(
                'w-full px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
                'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
                'border border-[var(--c-input-border)]',
                'focus:outline-none focus:border-[var(--c-input-border-focus)]',
              )}
            />
          )}

          {/* 每天：时间 */}
          {scheduleType === 'daily' && (
            <input
              type="time"
              value={timeStr}
              onChange={(e) => setTimeStr(e.target.value)}
              className={cn(
                'w-full px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
                'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
                'border border-[var(--c-input-border)]',
                'focus:outline-none focus:border-[var(--c-input-border-focus)]',
              )}
            />
          )}

          {/* 每周：星期多选 + 时间 */}
          {scheduleType === 'weekly' && (
            <div className="space-y-2">
              <div className="flex gap-1.5">
                {WEEKDAY_LABELS.map((w) => (
                  <button
                    key={w.value}
                    type="button"
                    onClick={() => toggleWeekday(w.value)}
                    className={cn(
                      'flex-1 py-1.5 text-xs rounded border transition-colors',
                      weekdays.includes(w.value)
                        ? 'bg-[var(--s-accent-soft)] text-[var(--s-accent)] border-[var(--s-accent)]'
                        : 'border-[var(--s-border-default)] text-[var(--s-text-secondary)] hover:bg-[var(--s-hover)]',
                    )}
                  >
                    {w.label}
                  </button>
                ))}
              </div>
              <input
                type="time"
                value={timeStr}
                onChange={(e) => setTimeStr(e.target.value)}
                className={cn(
                  'w-full px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
                  'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
                  'border border-[var(--c-input-border)]',
                  'focus:outline-none focus:border-[var(--c-input-border-focus)]',
                )}
              />
            </div>
          )}

          {/* 每月：日期下拉 + 时间 */}
          {scheduleType === 'monthly' && (
            <div className="flex gap-2">
              <select
                value={dayOfMonth}
                onChange={(e) => setDayOfMonth(Number(e.target.value))}
                className={cn(
                  'px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
                  'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
                  'border border-[var(--c-input-border)]',
                  'focus:outline-none focus:border-[var(--c-input-border-focus)]',
                )}
              >
                {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => (
                  <option key={d} value={d}>
                    {d} 日
                  </option>
                ))}
              </select>
              <input
                type="time"
                value={timeStr}
                onChange={(e) => setTimeStr(e.target.value)}
                className={cn(
                  'flex-1 px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
                  'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
                  'border border-[var(--c-input-border)]',
                  'focus:outline-none focus:border-[var(--c-input-border-focus)]',
                )}
              />
            </div>
          )}

          {/* 高级 cron */}
          {scheduleType === 'cron' && (
            <Input
              value={customCron}
              onChange={(e) => setCustomCron(e.target.value)}
              placeholder="0 9 * * *"
            />
          )}
        </div>

        {/* ── 推送目标 ── */}
        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-2">
            推送目标 *
          </label>
          <div className="space-y-2">
            {/* 板块1：推送给我自己 */}
            <PushTargetCard
              icon={<User className="w-4 h-4" />}
              title="推送给我自己"
              selected={pushMode === 'self'}
              onClick={() => setPushMode('self')}
            />

            {/* 板块2：推送给同事（仅管理员可见） */}
            {canPushToOthers && (
              <PushTargetCard
                icon={<Users className="w-4 h-4" />}
                title="推送给同事"
                selected={pushMode === 'colleague'}
                onClick={() => setPushMode('colleague')}
              >
                <select
                  value={colleagueId}
                  onChange={(e) => setColleagueId(e.target.value)}
                  disabled={pushMode !== 'colleague'}
                  className={cn(
                    'w-full mt-2 px-3 py-2 text-sm rounded',
                    'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
                    'border border-[var(--c-input-border)]',
                    'disabled:opacity-50',
                  )}
                >
                  <option value="">— 选择同事 —</option>
                  {colleagues
                    .filter((c) => c.user_id !== currentUserId && c.wecom_userid)
                    .map((c) => (
                      <option key={c.user_id} value={c.wecom_userid || ''}>
                        {c.nickname}
                      </option>
                    ))}
                </select>
              </PushTargetCard>
            )}

            {/* 板块3：推送到群聊（仅管理员可见） */}
            {canPushToOthers && (
              <PushTargetCard
                icon={<MessageSquare className="w-4 h-4" />}
                title="推送到群聊"
                selected={pushMode === 'group'}
                onClick={() => setPushMode('group')}
              >
                <select
                  value={groupId}
                  onChange={(e) => setGroupId(e.target.value)}
                  disabled={pushMode !== 'group'}
                  className={cn(
                    'w-full mt-2 px-3 py-2 text-sm rounded',
                    'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
                    'border border-[var(--c-input-border)]',
                    'disabled:opacity-50',
                  )}
                >
                  <option value="">— 选择群 —</option>
                  {groups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.chat_name || `未命名群 (${g.chatid.slice(0, 8)}...)`}
                    </option>
                  ))}
                </select>
              </PushTargetCard>
            )}
          </div>
        </div>

        {error && (
          <div className="text-xs text-[var(--s-error)] bg-[var(--s-error-soft)] px-3 py-2 rounded">
            {error}
          </div>
        )}
      </div>

      {/* 底部按钮 */}
      <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-[var(--s-border-default)]">
        <Button variant="secondary" size="sm" onClick={onClose}>
          取消
        </Button>
        <Button
          variant="accent"
          size="sm"
          loading={submitting}
          onClick={handleSubmit}
        >
          {isEdit ? '保存修改' : '创建任务'}
        </Button>
      </div>
    </>
  );
}

interface PushTargetCardProps {
  icon: React.ReactNode;
  title: string;
  selected: boolean;
  onClick: () => void;
  children?: React.ReactNode;
}

function PushTargetCard({ icon, title, selected, onClick, children }: PushTargetCardProps) {
  return (
    <div
      onClick={onClick}
      className={cn(
        'border rounded-md p-3 cursor-pointer transition-colors',
        selected
          ? 'border-[var(--s-accent)] bg-[var(--s-accent-soft)]'
          : 'border-[var(--s-border-default)] hover:bg-[var(--s-hover)]',
      )}
    >
      <div className="flex items-center gap-2">
        <div
          className={cn(
            'w-4 h-4 rounded-full border-2 flex-shrink-0',
            selected
              ? 'border-[var(--s-accent)] bg-[var(--s-accent)]'
              : 'border-[var(--s-border-default)]',
          )}
        />
        <span className="flex items-center gap-2 text-sm font-medium text-[var(--s-text-primary)]">
          {icon}
          {title}
        </span>
      </div>
      {children}
    </div>
  );
}

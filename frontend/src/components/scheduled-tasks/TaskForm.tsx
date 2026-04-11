/**
 * TaskForm — 创建/编辑定时任务表单
 *
 * Phase 9 会扩展：
 * - 自然语言输入框 + AI 解析
 * - 推送目标选择器（从 wecom_chat_targets 拉取）
 * - 模板文件上传
 * - 高级选项（积分上限/重试/超时）
 *
 * Phase 8 提供基础表单：name / prompt / cron / push_target
 */
import { useState, useEffect } from 'react';
import { ArrowLeft, Sparkles, Loader2 } from 'lucide-react';
import { Input } from '../ui/Input';
import { Button } from '../ui/Button';
import { useScheduledTaskStore } from '../../stores/useScheduledTaskStore';
import { scheduledTaskService } from '../../services/scheduledTask';
import { logger } from '../../utils/logger';
import { cn } from '../../utils/cn';
import type { ScheduledTask, CreateTaskDto, ChatTarget } from '../../types/scheduledTask';

interface Props {
  task: ScheduledTask | null;  // null = 新建
  onClose: () => void;
  onSaved: () => void;
}

const CRON_PRESETS = [
  { label: '每天 09:00', expr: '0 9 * * *' },
  { label: '每天 18:00', expr: '0 18 * * *' },
  { label: '每周一 09:00', expr: '0 9 * * 1' },
  { label: '每月 1 日 09:00', expr: '0 9 1 * *' },
];

export function TaskForm({ task, onClose, onSaved }: Props) {
  const isEdit = task !== null;
  const createTask = useScheduledTaskStore((s) => s.createTask);
  const updateTask = useScheduledTaskStore((s) => s.updateTask);

  const [name, setName] = useState(task?.name || '');
  const [prompt, setPrompt] = useState(task?.prompt || '');
  const [cronExpr, setCronExpr] = useState(task?.cron_expr || '0 9 * * *');
  const [chatId, setChatId] = useState(task?.push_target?.chatid || '');
  const [chatName, setChatName] = useState(task?.push_target?.chat_name || '');
  const [chatType, setChatType] = useState<'wecom_group' | 'wecom_user'>(
    (task?.push_target?.type as any) || 'wecom_group'
  );
  const [chatTargets, setChatTargets] = useState<ChatTarget[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 自然语言输入
  const [nlText, setNlText] = useState('');
  const [parsing, setParsing] = useState(false);

  // 拉取可用推送目标
  useEffect(() => {
    let cancelled = false;
    scheduledTaskService.listChatTargets()
      .then((targets) => {
        if (!cancelled) setChatTargets(targets);
      })
      .catch((err) => logger.error('task-form', '拉取推送目标失败', err));
    return () => { cancelled = true; };
  }, []);

  const handleNLParse = async () => {
    if (!nlText.trim()) return;
    setParsing(true);
    try {
      const result = await scheduledTaskService.parseNL(nlText);
      setName(result.name);
      setPrompt(result.prompt);
      setCronExpr(result.cron_expr);
      setNlText('');
    } catch (err) {
      logger.error('task-form', 'parse failed', err);
    } finally {
      setParsing(false);
    }
  };

  const handleSubmit = async () => {
    setError(null);

    if (!name.trim() || !prompt.trim() || !cronExpr.trim() || !chatId.trim()) {
      setError('请填写所有必填字段');
      return;
    }

    const dto: CreateTaskDto = {
      name: name.trim(),
      prompt: prompt.trim(),
      cron_expr: cronExpr.trim(),
      push_target: {
        type: chatType,
        chatid: chatId.trim(),
        chat_name: chatName.trim() || undefined,
      },
    };

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
          className={cn(
            'p-1 rounded',
            'text-[var(--s-text-tertiary)]',
            'hover:bg-[var(--s-hover)] hover:text-[var(--s-text-primary)]',
            'transition-colors',
          )}
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <h2 className="text-sm font-medium text-[var(--s-text-primary)]">
          {isEdit ? '编辑定时任务' : '新建定时任务'}
        </h2>
      </div>

      {/* 表单内容 */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {/* 自然语言输入（仅新建时） */}
        {!isEdit && (
          <div className="bg-[var(--s-surface-sunken)] rounded-lg p-3">
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="w-4 h-4 text-[var(--s-accent)]" />
              <span className="text-xs font-medium text-[var(--s-text-secondary)]">
                AI 智能创建
              </span>
            </div>
            <div className="flex gap-2">
              <Input
                value={nlText}
                onChange={(e) => setNlText(e.target.value)}
                placeholder="描述任务，如：每天9点推销售日报到运营群"
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
            rows={4}
            className={cn(
              'w-full px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
              'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
              'border border-[var(--c-input-border)]',
              'placeholder:text-[var(--c-input-placeholder)]',
              'focus:outline-none focus:border-[var(--c-input-border-focus)]',
              'focus:shadow-[var(--c-input-ring-focus)]',
              'transition-[border-color,box-shadow] duration-[var(--a-duration-normal)]',
            )}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5">
            执行频率 *
          </label>
          <div className="flex flex-wrap gap-2 mb-2">
            {CRON_PRESETS.map((p) => (
              <button
                key={p.expr}
                type="button"
                onClick={() => setCronExpr(p.expr)}
                className={cn(
                  'px-3 py-1.5 text-xs font-medium rounded-md border transition-colors',
                  cronExpr === p.expr
                    ? 'bg-[var(--s-accent-soft)] text-[var(--s-accent)] border-[var(--s-accent)]'
                    : 'border-[var(--s-border-default)] text-[var(--s-text-secondary)] hover:bg-[var(--s-hover)]',
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          <Input
            value={cronExpr}
            onChange={(e) => setCronExpr(e.target.value)}
            placeholder="0 9 * * *"
          />
          <p className="text-xs text-[var(--s-text-tertiary)] mt-1">
            自定义 cron 表达式（5 段格式）
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5">
            推送目标 *
          </label>
          {chatTargets.length > 0 ? (
            <select
              value={chatId}
              onChange={(e) => {
                const t = chatTargets.find((x) => x.chatid === e.target.value);
                if (t) {
                  setChatId(t.chatid);
                  setChatName(t.chat_name || '');
                  setChatType(t.chat_type === 'group' ? 'wecom_group' : 'wecom_user');
                }
              }}
              className={cn(
                'w-full px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
                'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
                'border border-[var(--c-input-border)]',
                'focus:outline-none focus:border-[var(--c-input-border-focus)]',
              )}
            >
              <option value="">选择群或单聊</option>
              {chatTargets.map((t) => (
                <option key={t.chatid} value={t.chatid}>
                  {t.chat_name || t.chatid} ({t.chat_type === 'group' ? '群' : '单聊'})
                </option>
              ))}
            </select>
          ) : (
            <>
              <Input
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
                placeholder="企微 chatid"
              />
              <p className="text-xs text-[var(--s-text-tertiary)] mt-1">
                暂无可用推送目标，需要先在企微中给机器人发过消息
              </p>
            </>
          )}
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

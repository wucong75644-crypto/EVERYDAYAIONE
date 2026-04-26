/**
 * 聊天内嵌表单块
 *
 * 渲染后端推送的 FormPart（定时任务创建/修改等）。
 * 支持字段联动（visible_when）、表单校验、WS 提交。
 *
 * 提交流程：
 * 1. 用户修改表单字段
 * 2. 点击确认 → 派发 chat:form-submit 自定义事件
 * 3. WebSocketContext 监听事件 → 发 form_submit WS 消息
 * 4. 后端处理后返回 form_submit_result → 前端 toast 提示
 */

import { memo, useState, useCallback, useMemo, type ChangeEvent } from 'react';
import { m, AnimatePresence } from 'framer-motion';
import { Calendar, CheckCircle2, X } from 'lucide-react';
import type { FormPart, FormField } from '../../../types/message';
import { cn } from '../../../utils/cn';
import { SOFT_SPRING } from '../../../utils/motion';

// ════════════════════════════════════════════════════════
// 子组件
// ════════════════════════════════════════════════════════

function TextField({
  field,
  value,
  onChange,
}: {
  field: FormField;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.placeholder}
      className={cn(
        'w-full rounded-[var(--s-radius-control)] border px-3 py-2 text-sm',
        'border-border-default bg-surface',
        'text-text-primary',
        'focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent',
        'transition-colors duration-150',
      )}
    />
  );
}

function TextareaField({
  field,
  value,
  onChange,
}: {
  field: FormField;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.placeholder}
      rows={3}
      className={cn(
        'w-full rounded-[var(--s-radius-control)] border px-3 py-2 text-sm resize-none',
        'border-border-default bg-surface',
        'text-text-primary',
        'focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent',
        'transition-colors duration-150',
      )}
    />
  );
}

function SelectField({
  field,
  value,
  onChange,
}: {
  field: FormField;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e: ChangeEvent<HTMLSelectElement>) => onChange(e.target.value)}
      className={cn(
        'w-full rounded-[var(--s-radius-control)] border px-3 py-2 text-sm',
        'border-border-default bg-surface',
        'text-text-primary',
        'focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent',
        'transition-colors duration-150 appearance-none',
        'bg-no-repeat bg-[length:16px] bg-[right_8px_center]',
      )}
      style={{
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")`,
      }}
    >
      {(field.options || []).map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

function TimeField({
  value,
  onChange,
}: {
  field: FormField;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="time"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        'w-40 rounded-[var(--s-radius-control)] border px-3 py-2 text-sm',
        'border-border-default bg-surface',
        'text-text-primary',
        'focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent',
        'transition-colors duration-150',
      )}
    />
  );
}

function NumberField({
  field,
  value,
  onChange,
}: {
  field: FormField;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="number"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.placeholder}
      min={1}
      max={31}
      className={cn(
        'w-24 rounded-[var(--s-radius-control)] border px-3 py-2 text-sm',
        'border-border-default bg-surface',
        'text-text-primary',
        'focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent',
        'transition-colors duration-150',
      )}
    />
  );
}

function CheckboxGroupField({
  field,
  value,
  onChange,
}: {
  field: FormField;
  value: number[];
  onChange: (v: number[]) => void;
}) {
  const toggle = (val: number) => {
    if (value.includes(val)) {
      onChange(value.filter((v) => v !== val));
    } else {
      onChange([...value, val].sort());
    }
  };

  return (
    <div className="flex flex-wrap gap-2">
      {(field.options || []).map((opt) => {
        const numVal = parseInt(opt.value, 10);
        const checked = value.includes(numVal);
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => toggle(numVal)}
            className={cn(
              'rounded-[var(--s-radius-control)] border px-3 py-1.5 text-sm',
              'transition-all duration-150',
              checked
                ? 'border-accent bg-accent text-text-on-accent'
                : 'border-border-default bg-surface text-text-secondary hover:border-accent hover:text-text-primary',
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// ════════════════════════════════════════════════════════
// 主组件
// ════════════════════════════════════════════════════════

interface FormBlockProps {
  form: FormPart;
}

export default memo(function FormBlock({ form }: FormBlockProps) {
  // 初始化表单值
  const initialValues = useMemo(() => {
    const vals: Record<string, unknown> = {};
    for (const field of form.fields) {
      vals[field.name] = field.default_value ?? '';
    }
    return vals;
  }, [form.fields]);

  const [values, setValues] = useState<Record<string, unknown>>(initialValues);
  const [status, setStatus] = useState<'idle' | 'submitting' | 'submitted' | 'cancelled'>('idle');
  const submitted = status === 'submitted';
  const submitting = status === 'submitting';
  const cancelled = status === 'cancelled';

  const updateField = useCallback((name: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  const handleSubmit = useCallback(() => {
    if (status !== 'idle') return;
    setStatus('submitting');

    // 派发自定义事件，WebSocketContext 监听处理
    window.dispatchEvent(
      new CustomEvent('chat:form-submit', {
        detail: {
          formType: form.form_type,
          formData: values,
        },
      }),
    );

    // 监听结果
    const handleResult = (e: Event) => {
      const { success, message } = (e as CustomEvent).detail;
      if (success) {
        setStatus('submitted');
      } else {
        setStatus('idle');
        alert(message || '提交失败');
      }
      window.removeEventListener('chat:form-submit-result', handleResult);
    };
    window.addEventListener('chat:form-submit-result', handleResult);

    // 超时兜底
    setTimeout(() => {
      window.removeEventListener('chat:form-submit-result', handleResult);
      setStatus((s) => (s === 'submitting' ? 'idle' : s));
    }, 15000);
  }, [form.form_type, values, status]);

  const handleCancel = useCallback(() => {
    setStatus('cancelled');
  }, []);

  // 判断字段是否可见（visible_when 联动）
  const isFieldVisible = useCallback(
    (field: FormField) => {
      if (!field.visible_when) return true;
      return String(values[field.visible_when.field]) === field.visible_when.value;
    },
    [values],
  );

  if (submitted || cancelled) {
    return (
      <m.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={SOFT_SPRING}
        className={cn(
          'my-2 flex items-center gap-2 rounded-[var(--s-radius-card)] border p-3 text-sm',
          submitted
            ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950/30 dark:text-green-300'
            : 'border-border-default bg-surface text-text-tertiary',
        )}
      >
        {submitted ? <CheckCircle2 size={16} /> : <X size={16} />}
        <span>{form.title} — {submitted ? '已提交' : '已取消'}</span>
      </m.div>
    );
  }

  return (
    <m.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={SOFT_SPRING}
      className={cn(
        'my-3 rounded-[var(--s-radius-card)] border overflow-hidden',
        'border-border-default bg-surface',
        'shadow-sm',
      )}
    >
      {/* 标题栏 */}
      <div
        className={cn(
          'flex items-center gap-2 px-4 py-3',
          'border-b border-border-default',
          'bg-surface-elevated',
        )}
      >
        <Calendar size={16} className="text-accent" />
        <span className="text-sm font-medium text-text-primary">
          {form.title}
        </span>
      </div>

      {/* 描述 */}
      {form.description && (
        <p className="px-4 pt-3 text-xs text-text-tertiary">
          {form.description}
        </p>
      )}

      {/* 字段 */}
      <div className="space-y-3 px-4 py-3">
        <AnimatePresence mode="sync">
          {form.fields.map((field) => {
            if (field.type === 'hidden') return null;
            if (!isFieldVisible(field)) return null;

            return (
              <m.div
                key={field.name}
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.15 }}
              >
                {field.label && (
                  <label className="mb-1 block text-xs font-medium text-text-secondary">
                    {field.label}
                    {field.required && (
                      <span className="ml-0.5 text-red-500">*</span>
                    )}
                  </label>
                )}
                {field.type === 'text' && (
                  <TextField
                    field={field}
                    value={String(values[field.name] ?? '')}
                    onChange={(v) => updateField(field.name, v)}
                  />
                )}
                {field.type === 'textarea' && (
                  <TextareaField
                    field={field}
                    value={String(values[field.name] ?? '')}
                    onChange={(v) => updateField(field.name, v)}
                  />
                )}
                {field.type === 'select' && (
                  <SelectField
                    field={field}
                    value={String(values[field.name] ?? '')}
                    onChange={(v) => updateField(field.name, v)}
                  />
                )}
                {field.type === 'time' && (
                  <TimeField
                    field={field}
                    value={String(values[field.name] ?? '')}
                    onChange={(v) => updateField(field.name, v)}
                  />
                )}
                {field.type === 'number' && (
                  <NumberField
                    field={field}
                    value={String(values[field.name] ?? '')}
                    onChange={(v) => updateField(field.name, v)}
                  />
                )}
                {field.type === 'checkbox_group' && (
                  <CheckboxGroupField
                    field={field}
                    value={
                      Array.isArray(values[field.name])
                        ? (values[field.name] as number[])
                        : []
                    }
                    onChange={(v) => updateField(field.name, v)}
                  />
                )}
              </m.div>
            );
          })}
        </AnimatePresence>
      </div>

      {/* 操作栏 */}
      <div
        className={cn(
          'flex items-center gap-3 px-4 py-3',
          'border-t border-border-default',
          'bg-surface-elevated',
        )}
      >
        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitting}
          className={cn(
            'flex flex-1 items-center justify-center gap-1.5 rounded-[var(--s-radius-control)] px-4 py-2 text-sm font-medium',
            'bg-accent text-text-on-accent',
            'hover:opacity-90 active:opacity-80',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            'transition-opacity duration-150',
          )}
        >
          {submitting ? (
            <>
              <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              提交中…
            </>
          ) : (
            <>
              <CheckCircle2 size={14} />
              {form.submit_text || '确认'}
            </>
          )}
        </button>
        <button
          type="button"
          onClick={handleCancel}
          className={cn(
            'flex items-center justify-center gap-1.5 rounded-[var(--s-radius-control)] px-4 py-2 text-sm',
            'border border-border-default',
            'text-text-secondary hover:text-text-primary',
            'hover:bg-hover',
            'transition-colors duration-150',
          )}
        >
          <X size={14} />
          {form.cancel_text || '取消'}
        </button>
      </div>
    </m.div>
  );
});

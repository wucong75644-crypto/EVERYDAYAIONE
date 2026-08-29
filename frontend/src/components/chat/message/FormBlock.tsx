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

import { memo, useState, useCallback, useEffect, useMemo, type ChangeEvent } from 'react';
import { m, AnimatePresence } from 'framer-motion';
import { CheckCircle2, Circle, Loader2, X } from 'lucide-react';
import type { FormPart, FormField } from '../../../types/message';
import { cn } from '../../../utils/cn';
import { formatFormValue } from '../../../utils/displayValue';
import { SOFT_SPRING } from '../../../utils/motion';
import { FormBlockContent } from './FormBlockContent';

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
  messageId: string;
  conversationId: string;
}

type FormStatus = 'open' | 'submitting' | 'submitted' | 'cancelled';

function ScheduledTaskWorkflowStage({
  formType,
  status,
}: {
  formType: string;
  status: FormStatus;
}) {
  if (!['scheduled_task_create', 'scheduled_task_confirm'].includes(formType)) return null;

  const activeStep = formType === 'scheduled_task_confirm'
    ? (status === 'submitted' ? 4 : 3)
    : (status === 'submitting' ? 2 : status === 'submitted' ? 3 : 1);
  const labels = ['填写配置', '规划与试跑', '确认启用', '已启用'];

  return (
    <div className="mx-4 mt-3 rounded-[var(--s-radius-card)] border border-border-default bg-surface px-3 py-2">
      <div className="flex items-center gap-1 overflow-x-auto" aria-label={`定时任务第 ${activeStep} 步，共 4 步`}>
        {labels.map((label, index) => {
          const step = index + 1;
          const complete = step < activeStep;
          const active = step === activeStep;
          return (
            <div key={label} className="flex min-w-0 items-center gap-1.5">
              {index > 0 && <span className="h-px w-3 shrink-0 bg-border-default" aria-hidden="true" />}
              {active && status === 'submitting'
                ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
                : complete
                  ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />
                  : <Circle className={cn('h-3.5 w-3.5 shrink-0', active ? 'text-accent' : 'text-text-tertiary')} />}
              <span className={cn('whitespace-nowrap text-[11px]', active ? 'font-medium text-text-primary' : complete ? 'text-text-secondary' : 'text-text-tertiary')}>
                {step}. {label}
              </span>
            </div>
          );
        })}
      </div>
      <p className="mt-1 text-[11px] text-text-secondary">
        {activeStep === 1 && '尚未创建任务；请确认配置后开始规划与安全试跑。'}
        {activeStep === 2 && '正在进行只读试跑：不扣积分、不发送消息、不写入业务数据。'}
        {activeStep === 3 && '预检已通过；确认启用后才会创建正式任务。'}
        {activeStep === 4 && '任务已启用，可在定时任务面板查看执行历史。'}
      </p>
    </div>
  );
}

function FormFields({
  fields,
  values,
  isVisible,
  onChange,
}: {
  fields: FormField[];
  values: Record<string, unknown>;
  isVisible: (field: FormField) => boolean;
  onChange: (name: string, value: unknown) => void;
}) {
  const renderField = (field: FormField) => {
    const value = formatFormValue(values[field.name]);
    const update = (next: unknown) => onChange(field.name, next);
    if (field.type === 'text') return <TextField field={field} value={value} onChange={update} />;
    if (field.type === 'textarea') return <TextareaField field={field} value={value} onChange={update} />;
    if (field.type === 'select') return <SelectField field={field} value={value} onChange={update} />;
    if (field.type === 'time') return <TimeField field={field} value={value} onChange={update} />;
    if (field.type === 'number') return <NumberField field={field} value={value} onChange={update} />;
    if (field.type === 'checkbox_group') {
      const selected = Array.isArray(values[field.name]) ? values[field.name] as number[] : [];
      return <CheckboxGroupField field={field} value={selected} onChange={update} />;
    }
    return null;
  };

  return (
    <div className="space-y-3 px-4 py-3">
      <AnimatePresence mode="sync">
        {fields.map((field) => {
          if (field.type === 'hidden' || !isVisible(field)) return null;
          return (
            <m.div key={field.name} initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.15 }}>
              {field.label && (
                <label className="mb-1 block text-xs font-medium text-text-secondary">
                  {field.label}{field.required && <span className="ml-0.5 text-red-500">*</span>}
                </label>
              )}
              {renderField(field)}
            </m.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

export default memo(function FormBlock({ form, messageId, conversationId }: FormBlockProps) {
  // 初始化表单值
  const initialValues = useMemo(() => {
    const vals: Record<string, unknown> = {};
    for (const field of form.fields) {
      vals[field.name] = field.default_value ?? '';
    }
    return vals;
  }, [form.fields]);

  const [values, setValues] = useState<Record<string, unknown>>(initialValues);
  const [status, setStatus] = useState<FormStatus>(form.status || 'open');
  const [nextForm, setNextForm] = useState<FormPart | null>(form.next_form || null);
  const [submittedMessage, setSubmittedMessage] = useState(form.result_message || '');
  const [formError, setFormError] = useState(form.error_message || '');
  const submitted = status === 'submitted';
  const submitting = status === 'submitting';
  const cancelled = status === 'cancelled';

  useEffect(() => {
    setStatus(form.status || 'open');
    setNextForm(form.next_form || null);
    setSubmittedMessage(form.result_message || '');
    setFormError(form.error_message || '');
  }, [form.status, form.next_form, form.result_message, form.error_message]);

  useEffect(() => {
    const handleResult = (e: Event) => {
      const detail = (e as CustomEvent).detail as {
        success?: boolean;
        message?: string;
        status?: FormStatus;
        form_id?: string;
        message_id?: string;
        conversation_id?: string;
        next_form?: FormPart;
      };
      if (
        detail.form_id !== form.form_id
        || detail.message_id !== messageId
        || detail.conversation_id !== conversationId
      ) return;

      if (detail.success) {
        const resolved = detail.status || 'submitted';
        setStatus(resolved);
        setSubmittedMessage(detail.message || '');
        if (detail.next_form) setNextForm(detail.next_form);
      } else {
        setStatus(detail.status === 'cancelled' ? 'cancelled' : 'open');
        setFormError(detail.message || '提交失败，请重试');
      }
    };
    window.addEventListener('chat:form-submit-result', handleResult);
    return () => window.removeEventListener('chat:form-submit-result', handleResult);
  }, [conversationId, form.form_id, messageId]);

  const updateField = useCallback((name: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  const handleSubmit = useCallback(() => {
    if (status !== 'open') return;
    setStatus('submitting');
    setFormError('');

    window.dispatchEvent(
      new CustomEvent('chat:form-submit', {
        detail: {
          formType: form.form_type,
          formData: values,
          formId: form.form_id,
          messageId,
          conversationId,
          action: 'submit',
        },
      }),
    );
  }, [conversationId, form.form_id, form.form_type, messageId, status, values]);

  const handleCancel = useCallback(() => {
    if (status !== 'open') return;
    setStatus('submitting');
    window.dispatchEvent(
      new CustomEvent('chat:form-submit', {
        detail: {
          formType: form.form_type,
          formData: {},
          formId: form.form_id,
          messageId,
          conversationId,
          action: 'cancel',
        },
      }),
    );
  }, [conversationId, form.form_id, form.form_type, messageId, status]);

  // 判断字段是否可见（visible_when 联动）
  const isFieldVisible = useCallback(
    (field: FormField) => {
      if (!field.visible_when) return true;
      return formatFormValue(values[field.visible_when.field]) === field.visible_when.value;
    },
    [values],
  );

  if (submitted || cancelled) {
    return (
      <>
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
          <span>{submitted ? (submittedMessage || `${form.title} — 已提交`) : `${form.title} — 已取消`}</span>
        </m.div>
        {nextForm && <FormBlock form={nextForm} messageId={messageId} conversationId={conversationId} />}
      </>
    );
  }

  return (
    <>
      <ScheduledTaskWorkflowStage formType={form.form_type} status={status} />
      <FormBlockContent form={form} submitting={submitting}
        onSubmit={handleSubmit} onCancel={handleCancel}
        fields={<FormFields fields={form.fields} values={values}
          isVisible={isFieldVisible} onChange={updateField} />} />
      {formError && <p className="mx-4 mt-2 text-xs text-red-600 dark:text-red-400">{formError}</p>}
    </>
  );
});

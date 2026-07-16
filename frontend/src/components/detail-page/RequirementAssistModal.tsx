import { AlertTriangle, ImageIcon, RefreshCw, Sparkles } from 'lucide-react';

import type { RequirementAssistResult, RequirementSuggestionId } from '../../types/ecomRequirement';
import { cn } from '../../utils/cn';
import Modal from '../common/Modal';
import { Button } from '../ui/Button';

interface RequirementAssistModalProps {
  isOpen: boolean;
  isLoading: boolean;
  result: RequirementAssistResult | null;
  selectedId: RequirementSuggestionId;
  selectedBrief: string;
  error: string | null;
  onClose: () => void;
  onSelect: (id: RequirementSuggestionId) => void;
  onDraftChange: (id: RequirementSuggestionId, value: string) => void;
  onRegenerate: () => void;
  onConfirm: (brief: string) => void;
}

function LoadingState() {
  return (
    <div className="min-h-[360px] flex flex-col items-center justify-center text-center">
      <span className="w-11 h-11 rounded-full bg-[var(--s-surface-subtle)] flex items-center justify-center">
        <RefreshCw className="w-5 h-5 animate-spin text-[var(--s-text-secondary)]" />
      </span>
      <p className="mt-4 text-base font-medium text-[var(--s-text-primary)]">正在分析产品图片…</p>
      <p className="mt-1 text-sm text-[var(--s-text-tertiary)]">AI 正在核对产品事实并生成三套创作方案</p>
    </div>
  );
}

function InsightSummary({ result }: { result: RequirementAssistResult }) {
  return (
    <div className="grid gap-2 lg:grid-cols-2">
      <section className="rounded-[var(--s-radius-card)] border border-[var(--s-border-subtle)] bg-[var(--s-surface-subtle)] p-3">
        <h3 className="text-sm font-semibold text-[var(--s-text-primary)]">产品识别</h3>
        <p className="mt-1 text-sm text-[var(--s-text-secondary)]">{result.product_facts.product_name}</p>
        {result.product_facts.confirmed_attributes.length > 0 && (
          <p className="mt-1 text-xs leading-5 text-[var(--s-text-tertiary)]">{result.product_facts.confirmed_attributes.join(' · ')}</p>
        )}
      </section>
      <section className="rounded-[var(--s-radius-card)] border border-[var(--s-border-subtle)] bg-[var(--s-surface-subtle)] p-3">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--s-text-primary)]">
          <ImageIcon className="w-4 h-4" />参考图理解
        </h3>
        <p className="mt-1 text-xs leading-5 text-[var(--s-text-tertiary)]">
          {result.reference_analyses.length > 0
            ? result.reference_analyses.map((item) => item.summary).join(' · ')
            : '未上传参考图，将根据产品事实和用户要求规划视觉方向。'}
        </p>
      </section>
    </div>
  );
}

function ConflictNotice({ result }: { result: RequirementAssistResult }) {
  if (result.conflicts.length === 0 && result.product_facts.unclear_items.length === 0) return null;
  return (
    <div className="rounded-[var(--s-radius-card)] border border-amber-300/70 bg-amber-50 px-3 py-2.5 text-amber-900">
      <p className="flex items-center gap-1.5 text-sm font-semibold">
        <AlertTriangle className="w-4 h-4 shrink-0" />待确认信息
      </p>
      <ul className="mt-1 space-y-0.5 text-xs leading-5">
        {result.conflicts.map((conflict) => <li key={`${conflict.field}-${conflict.user_value}`}>• {conflict.message}</li>)}
        {result.product_facts.unclear_items.map((item) => <li key={item}>• {item}</li>)}
      </ul>
    </div>
  );
}

function SchemeEditor({
  result, selectedId, selectedBrief, disabled, onSelect, onDraftChange,
}: Pick<RequirementAssistModalProps, 'result' | 'selectedId' | 'selectedBrief' | 'onSelect' | 'onDraftChange'> & { disabled: boolean }) {
  if (!result) return null;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="AI帮写方案">
        <span className="mr-1 text-sm text-[var(--s-text-secondary)]">方案选择：</span>
        {result.suggestions.map((suggestion) => (
          <button
            key={suggestion.id}
            type="button"
            role="tab"
            aria-selected={selectedId === suggestion.id}
            disabled={disabled}
            onClick={() => onSelect(suggestion.id)}
            className={cn(
              'rounded-full border px-4 py-1.5 text-sm font-medium transition-colors disabled:opacity-50',
              selectedId === suggestion.id
                ? 'border-transparent bg-[var(--c-button-primary-bg)] text-[var(--c-button-primary-fg)]'
                : 'border-[var(--s-border-default)] bg-[var(--s-surface-card)] text-[var(--s-text-secondary)] hover:bg-[var(--s-hover)]',
            )}
          >
            {suggestion.name}
          </button>
        ))}
      </div>
      <label htmlFor="requirement-assist-brief" className="sr-only">当前方案创作简报</label>
      <textarea
        id="requirement-assist-brief"
        value={selectedBrief}
        disabled={disabled}
        onChange={(event) => onDraftChange(selectedId, event.target.value)}
        className="mt-3 min-h-[260px] w-full resize-y rounded-[var(--c-input-radius)] border border-[var(--c-input-border)] bg-[var(--c-input-bg)] px-4 py-3 text-sm leading-6 text-[var(--c-input-fg)] focus:outline-none focus:border-[var(--c-input-border-focus)] disabled:opacity-60"
      />
    </div>
  );
}

export function RequirementAssistModal(props: RequirementAssistModalProps) {
  const hasResult = Boolean(props.result);
  return (
    <Modal isOpen={props.isOpen} onClose={props.onClose} title="AI帮写方案选择" maxWidth="max-w-4xl">
      <div className="flex max-h-[78vh] flex-col">
        <p className="-mt-1 mb-4 text-sm text-[var(--s-text-tertiary)]">选择方案后可自由编辑，确认即可用于后续产品分析</p>
        {!hasResult && props.isLoading ? <LoadingState /> : (
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
            {props.error && (
              <div role="alert" className="rounded-[var(--s-radius-card)] border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{props.error}</div>
            )}
            {!hasResult && !props.isLoading && (
              <div className="min-h-[280px] flex flex-col items-center justify-center text-center">
                <p className="text-sm text-[var(--s-text-secondary)]">暂时无法生成方案，请重新尝试。</p>
              </div>
            )}
            {props.result && <InsightSummary result={props.result} />}
            {props.result && <ConflictNotice result={props.result} />}
            <SchemeEditor {...props} disabled={props.isLoading} />
          </div>
        )}
        <div className="mt-4 flex flex-col gap-2 border-t border-[var(--s-border-subtle)] pt-4 sm:flex-row sm:items-center">
          <Button variant="secondary" icon={<RefreshCw className="w-4 h-4" />} loading={props.isLoading && hasResult} disabled={props.isLoading && !hasResult} onClick={props.onRegenerate}>重新帮写</Button>
          <Button className="sm:ml-auto sm:min-w-40" icon={<Sparkles className="w-4 h-4" />} disabled={props.isLoading || !props.selectedBrief.trim()} onClick={() => props.onConfirm(props.selectedBrief)}>确认选择</Button>
        </div>
      </div>
    </Modal>
  );
}

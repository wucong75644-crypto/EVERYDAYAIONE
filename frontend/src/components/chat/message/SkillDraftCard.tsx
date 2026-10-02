import { useCallback, useEffect, useState } from 'react';
import { BookOpen, Check, ChevronDown, CircleAlert, Loader2, Pencil, Play, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import type { ChangeSet } from '../../../types/changeset';
import { changeSetService } from '../../../services/changeSet';
import { skillCreationService, type SkillCandidateEdit, type SkillTrialEstimate, type SkillTrialResult } from '../../../services/skillCreation';
import { Button } from '../../ui/Button';
import { cn } from '../../../utils/cn';

interface Props { changeSetId: string; fallbackTitle?: string }

const MODE_LABELS: Record<string, string> = {
  smart: '智能', 'image-i2i': '图生图', 'image-t2i': '文生图', 'image-ecom': '电商图', video: '视频',
};
const TASK_MODE_OPTIONS = Object.entries(MODE_LABELS);

function parseList(value: string, limit: number): string[] {
  return value.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean).slice(0, limit);
}

function initialEdit(changeSet: ChangeSet): SkillCandidateEdit {
  const snapshot = changeSet.proposed_snapshot;
  const content = (snapshot.content || {}) as Record<string, unknown>;
  const metadata = (content.catalog_metadata || {}) as Record<string, unknown>;
  return {
    expected_revision: changeSet.revision,
    name: String(snapshot.name || metadata.name || ''),
    description: String(snapshot.description || content.description || ''),
    body: String(content.body || ''),
    task_modes: Array.isArray(snapshot.task_modes) ? snapshot.task_modes.map(String) : ['smart'],
    triggers: Array.isArray(snapshot.triggers) ? snapshot.triggers.map(String) : [],
    input_requirements: Array.isArray(snapshot.input_requirements) ? snapshot.input_requirements.map(String) : [],
    open_questions: Array.isArray(snapshot.open_questions) ? snapshot.open_questions.map(String) : [],
  };
}

export default function SkillDraftCard({ changeSetId, fallbackTitle }: Props) {
  const [changeSet, setChangeSet] = useState<ChangeSet | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<SkillCandidateEdit | null>(null);
  const [pending, setPending] = useState<'load' | 'edit' | 'confirm' | 'cancel' | null>('load');
  const [error, setError] = useState('');
  const [trialEnabled, setTrialEnabled] = useState(false);
  const [trialKind, setTrialKind] = useState<'text' | 'image' | null>(null);
  const [trialEstimate, setTrialEstimate] = useState<SkillTrialEstimate | null>(null);
  const [trialInput, setTrialInput] = useState('');
  const [trialRatio, setTrialRatio] = useState('1:1');
  const [trialImages, setTrialImages] = useState<string[]>([]);
  const [trialResult, setTrialResult] = useState<SkillTrialResult | null>(null);
  const [trialPending, setTrialPending] = useState(false);
  const [trialError, setTrialError] = useState('');
  const [trialKey, setTrialKey] = useState<string | null>(null);
  const [trialFeedback, setTrialFeedback] = useState<'helpful' | 'not_helpful' | null>(null);

  const refresh = useCallback(async () => {
    try {
      const latest = await changeSetService.get(changeSetId);
      setChangeSet(latest);
      setForm(initialEdit(latest));
      try {
        const history = await skillCreationService.listTrials(changeSetId);
        setTrialEnabled(history.enabled);
        const mostRecent = history.runs[0];
        if (mostRecent) {
          setTrialResult(mostRecent);
          setTrialFeedback(mostRecent.feedback_rating || null);
          setTrialKind(mostRecent.mode);
        }
      } catch {
        setTrialEnabled(false);
      }
      setError('');
    } catch {
      setError('暂时无法读取候选状态，请稍后刷新页面。');
    } finally {
      setPending((current) => current === 'load' ? null : current);
    }
  }, [changeSetId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const runAction = async (action: 'confirm' | 'cancel' | 'edit') => {
    if (!changeSet) return;
    setPending(action);
    setError('');
    try {
      if (action === 'edit') {
        if (!form) return;
        const updated = await skillCreationService.revise(changeSetId, form);
        setChangeSet(updated);
        setForm(initialEdit(updated));
        setEditing(false);
      } else if (action === 'confirm') {
        const updated = await changeSetService.confirm(changeSetId, {
          expected_change_set_revision: changeSet.revision,
          content_sha256: String(changeSet.proposed_snapshot.content_sha256 || ''),
        });
        setChangeSet(updated);
      } else {
        const updated = await changeSetService.cancel(changeSetId, '用户取消 Skill 草稿创建');
        setChangeSet(updated);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败，请刷新后重试。');
      await refresh();
    } finally {
      setPending(null);
    }
  };

  const openTrial = async (kind: 'text' | 'image') => {
    setTrialKind(kind);
    setTrialResult(null);
    setTrialError('');
    setTrialFeedback(null);
    setTrialKey(null);
    if (kind === 'image') {
      try {
        setTrialEstimate(await skillCreationService.estimateTrial(changeSetId));
      } catch (reason) {
        setTrialError(reason instanceof Error ? reason.message : '无法读取试用参数。');
      }
    }
  };

  const runTrial = async () => {
    if (!changeSet || !trialKind || !trialInput.trim()) return;
    const key = trialKey || crypto.randomUUID();
    setTrialKey(key);
    setTrialPending(true);
    setTrialError('');
    try {
      const result = await skillCreationService.runTrial(changeSetId, {
        expected_revision: changeSet.revision,
        content_sha256: String(changeSet.proposed_snapshot.content_sha256 || ''),
        mode: trialKind,
        input_text: trialInput,
        idempotency_key: key,
        ...(trialKind === 'image' ? {
          aspect_ratio: trialRatio,
          reference_image_urls: trialImages,
        } : {}),
      });
      setTrialResult(result);
      setTrialKey(null);
    } catch (reason) {
      setTrialError(reason instanceof Error ? reason.message : '试用失败，请检查后重试。');
    } finally {
      setTrialPending(false);
    }
  };

  const sendTrialFeedback = async (rating: 'helpful' | 'not_helpful') => {
    if (!trialResult) return;
    try {
      await skillCreationService.feedback(trialResult.trial_id, rating);
      setTrialFeedback(rating);
    } catch {
      setTrialError('反馈暂未保存，请稍后重试。');
    }
  };

  if (!changeSet) return (
    <div className="my-3 flex items-center gap-2 rounded-xl border border-border-default bg-surface px-4 py-3 text-sm text-text-secondary">
      {pending === 'load' && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
      {error || '正在读取 Skill 候选…'}
    </div>
  );

  const snapshot = changeSet.proposed_snapshot;
  const content = (snapshot.content || {}) as Record<string, unknown>;
  const status = changeSet.status;
  const awaiting = status === 'awaiting_approval';
  const applied = status === 'applied';
  const isUpdate = changeSet.operation === 'update';
  const targetSkill = (snapshot.target_skill || {}) as Record<string, unknown>;
  const sourceScope = String(changeSet.audit_subject?.source_scope || 'recent_40_messages');
  const sourceCount = Array.isArray(changeSet.audit_subject?.source_message_refs)
    ? changeSet.audit_subject.source_message_refs.length : 0;
  const modes = Array.isArray(snapshot.task_modes) ? snapshot.task_modes.map(String) : [];
  const inputs = Array.isArray(snapshot.input_requirements) ? snapshot.input_requirements.map(String) : [];
  const questions = Array.isArray(snapshot.open_questions) ? snapshot.open_questions.map(String) : [];
  const trialIsForOlderCandidate = !!trialResult
    && trialResult.content_sha256 !== String(snapshot.content_sha256 || '');

  return (
    <section className="my-3 min-w-0 overflow-hidden rounded-2xl border border-border-default bg-surface shadow-sm" aria-label="Skill 创建预览">
      <header className="flex items-start gap-3 border-b border-border-default bg-surface-elevated px-4 py-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent">
          <BookOpen className="h-4 w-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="break-words text-sm font-semibold text-text-primary">{String(snapshot.name || fallbackTitle || 'Skill 创建预览')}</h3>
          <p className="mt-1 text-xs text-text-secondary">{applied ? `草稿已${isUpdate ? '更新' : '创建'}，后续可提交审核发布。` : awaiting ? (isUpdate ? '核对更新内容，确认后只更新现有草稿。' : '核对整理结果，确认后只创建草稿。') : `当前状态：${status}`}</p>
        </div>
        <span className={cn('shrink-0 rounded-full px-2 py-1 text-[11px] font-medium', applied ? 'bg-success/10 text-success' : awaiting ? 'bg-active text-text-secondary' : 'bg-warning/10 text-warning')}>
          {applied ? '草稿已创建' : awaiting ? '待确认' : status}
        </span>
      </header>

      <div className="space-y-3 px-4 py-4">
        {editing && form ? (
          <div className="space-y-3">
            <label className="block text-xs font-medium text-text-secondary">Skill 名称
              <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} className="mt-1 w-full rounded-lg border border-border-default bg-surface px-3 py-2 text-sm text-text-primary" maxLength={200} />
            </label>
            <label className="block text-xs font-medium text-text-secondary">用途
              <input value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} className="mt-1 w-full rounded-lg border border-border-default bg-surface px-3 py-2 text-sm text-text-primary" maxLength={2000} />
            </label>
            <label className="block text-xs font-medium text-text-secondary">触发方式（用顿号分隔）
              <input value={form.triggers.join('、')} onChange={(event) => setForm({ ...form, triggers: parseList(event.target.value, 12) })} className="mt-1 w-full rounded-lg border border-border-default bg-surface px-3 py-2 text-sm text-text-primary" maxLength={1000} />
            </label>
            <fieldset className="block text-xs font-medium text-text-secondary">
              <legend>适用模式</legend>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
                {TASK_MODE_OPTIONS.map(([mode, label]) => <label key={mode} className="inline-flex items-center gap-1.5">
                  <input type="checkbox" checked={form.task_modes.includes(mode)} onChange={(event) => {
                    const task_modes = event.target.checked
                      ? [...form.task_modes, mode]
                      : form.task_modes.filter((item) => item !== mode);
                    if (task_modes.length) setForm({ ...form, task_modes });
                  }} />
                  {label}
                </label>)}
              </div>
            </fieldset>
            <label className="block text-xs font-medium text-text-secondary">每次需要提供（每行一项）
              <textarea value={form.input_requirements.join('\n')} onChange={(event) => setForm({ ...form, input_requirements: parseList(event.target.value, 12) })} className="mt-1 min-h-20 w-full resize-y rounded-lg border border-border-default bg-surface px-3 py-2 text-sm text-text-primary" maxLength={6000} />
            </label>
            <label className="block text-xs font-medium text-text-secondary">完整提示词
              <textarea value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} className="mt-1 min-h-56 w-full resize-y rounded-lg border border-border-default bg-surface px-3 py-2 text-sm leading-6 text-text-primary" maxLength={50000} />
            </label>
            <label className="block text-xs font-medium text-text-secondary">待确认问题（每行一项）
              <textarea value={form.open_questions.join('\n')} onChange={(event) => setForm({ ...form, open_questions: parseList(event.target.value, 16) })} className="mt-1 min-h-20 w-full resize-y rounded-lg border border-border-default bg-surface px-3 py-2 text-sm text-text-primary" maxLength={8000} />
            </label>
          </div>
        ) : (
          <>
            <p className="break-words text-sm text-text-primary">{String(snapshot.description || content.description || '尚未填写用途')}</p>
            <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
              <div><div className="mb-1 text-text-tertiary">适用模式</div><div className="flex flex-wrap gap-1.5">{modes.map((mode) => <span key={mode} className="rounded-full bg-active px-2 py-1 text-text-secondary">{MODE_LABELS[mode] || mode}</span>)}</div></div>
              <div><div className="mb-1 text-text-tertiary">保存位置</div><div className="text-text-secondary">当前组织</div></div>
              <div><div className="mb-1 text-text-tertiary">整理范围</div><div className="text-text-secondary">{sourceScope === 'selected_assistant_turn' ? `所选回答关联的本轮对话（${sourceCount} 条）` : sourceCount ? `当前会话最近 ${sourceCount} 条消息` : '当前会话消息'}</div></div>
              {isUpdate && <div className="sm:col-span-2"><div className="mb-1 text-text-tertiary">更新目标</div><div className="text-text-secondary">{String(targetSkill.name || targetSkill.skill_key || '现有 Skill')} · 当前草稿版本 v{String(targetSkill.expected_version || changeSet.base_revision)}</div></div>}
              <div className="sm:col-span-2"><div className="mb-1 text-text-tertiary">每次需要提供</div><div className="text-text-secondary">{inputs.length ? inputs.join('、') : '按任务需要提供资料'}</div></div>
            </div>
            {Array.isArray(snapshot.triggers) && snapshot.triggers.length > 0 && <div><div className="mb-1 text-xs text-text-tertiary">适合在这些需求下使用</div><div className="flex flex-wrap gap-1.5">{snapshot.triggers.map((trigger: string) => <span key={trigger} className="rounded-full border border-border-default px-2 py-1 text-xs text-text-secondary">{trigger}</span>)}</div></div>}
            {questions.length > 0 && <div className="rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning"><div className="mb-1 font-medium">还有待确认的内容</div>{questions.map((question) => <div key={question}>· {question}</div>)}</div>}
            <details className="group rounded-lg border border-border-default/70">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 text-xs font-medium text-text-secondary [&::-webkit-details-marker]:hidden">查看完整提示词<ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" aria-hidden="true" /></summary>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t border-border-default/70 px-3 py-3 text-xs leading-5 text-text-primary">{String(content.body || '')}</pre>
            </details>
          </>
        )}
        {awaiting && trialKind && <div className="space-y-3 rounded-xl border border-border-default bg-surface-elevated p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium text-text-primary">{trialKind === 'text' ? '文本试用' : '图片试用'}</div>
            <button type="button" className="text-xs text-text-secondary hover:text-text-primary" onClick={() => { setTrialKind(null); setTrialResult(null); setTrialError(''); }}>关闭</button>
          </div>
          <label className="block text-xs text-text-secondary">输入一组实际任务资料
            <textarea value={trialInput} onChange={(event) => setTrialInput(event.target.value)} className="mt-1 min-h-20 w-full resize-y rounded-lg border border-border-default bg-surface px-3 py-2 text-sm text-text-primary" maxLength={8000} placeholder="只用于本次预览，不会写入 Skill 正文" />
          </label>
          {trialKind === 'image' && <>
            <div className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
              <label>比例
                <select value={trialRatio} onChange={(event) => setTrialRatio(event.target.value)} className="ml-2 rounded-md border border-border-default bg-surface px-2 py-1 text-text-primary">
                  {['1:1', '3:2', '2:3', '4:3', '3:4', '16:9', '9:16'].map((ratio) => <option key={ratio}>{ratio}</option>)}
                </select>
              </label>
              <span>{trialImages.length ? `单张图片试用，预计 ${trialEstimate?.image_to_image.estimated_credits ?? '…'} 积分` : `单张图片试用，预计 ${trialEstimate?.text_to_image.estimated_credits ?? '…'} 积分`}</span>
            </div>
            {!!trialEstimate?.reference_images.length && <div className="space-y-1.5">
              <div className="text-xs text-text-secondary">可选用本会话中你上传的参考图</div>
              {trialEstimate.reference_images.map((image) => <label key={`${image.message_id}:${image.url}`} className="flex cursor-pointer items-center gap-2 text-xs text-text-secondary">
                <input type="checkbox" checked={trialImages.includes(image.url)} onChange={(event) => setTrialImages((current) => event.target.checked ? [...current, image.url].slice(0, 8) : current.filter((url) => url !== image.url))} />
                <img src={image.preview_url} alt="" className="h-9 w-9 rounded-md object-cover" />
                <span className="truncate">{image.name}</span>
              </label>)}
            </div>}
            <p className="text-xs text-text-tertiary">点击试用会调用图片模型并按上方预估积分计费；每次生成 1 张。</p>
          </>}
          {trialError && <p role="alert" className="text-xs text-error">{trialError}</p>}
          <Button size="sm" variant="accent" onClick={() => void runTrial()} loading={trialPending} disabled={trialPending || !trialInput.trim() || (trialKind === 'image' && !trialEstimate)} icon={<Play className="h-3.5 w-3.5" />}>开始试用</Button>
          {trialResult && <div className="space-y-2 rounded-lg border border-border-default bg-surface p-3">
            {trialIsForOlderCandidate && <p className="text-xs text-warning">这是旧候选版本的试用结果，当前内容已变化。</p>}
            <div className="whitespace-pre-wrap break-words text-sm leading-6 text-text-primary">{trialResult.output}</div>
            {trialResult.images?.map((image) => <img key={image.url} src={image.url} alt={image.name} className="max-h-96 max-w-full rounded-lg object-contain" />)}
            <div className="flex items-center gap-2 border-t border-border-default pt-2 text-xs text-text-secondary">
              <span>这次结果有帮助吗？</span>
              <button type="button" onClick={() => void sendTrialFeedback('helpful')} aria-label="试用结果有帮助" className={cn('rounded-md p-1.5 hover:bg-active', trialFeedback === 'helpful' && 'text-accent')}><ThumbsUp className="h-4 w-4" /></button>
              <button type="button" onClick={() => void sendTrialFeedback('not_helpful')} aria-label="试用结果没有帮助" className={cn('rounded-md p-1.5 hover:bg-active', trialFeedback === 'not_helpful' && 'text-accent')}><ThumbsDown className="h-4 w-4" /></button>
              {trialFeedback && <span>已记录</span>}
            </div>
          </div>}
        </div>}
        {error && <p className="flex items-start gap-2 text-xs text-error" role="alert"><CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{error}</p>}
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-border-default bg-surface-elevated px-4 py-3">
        {awaiting && editing && <Button size="sm" variant="accent" onClick={() => void runAction('edit')} loading={pending === 'edit'} disabled={!form?.name.trim() || !form?.body.trim()} icon={<Check className="h-3.5 w-3.5" />}>更新预览</Button>}
        {awaiting && !editing && <>
          {trialEnabled && <Button size="sm" variant="secondary" onClick={() => void openTrial('text')} disabled={!!pending || trialPending} icon={<Play className="h-3.5 w-3.5" />}>文本试用</Button>}
          {trialEnabled && modes.some((mode) => ['image-i2i', 'image-t2i', 'image-ecom'].includes(mode)) && <Button size="sm" variant="secondary" onClick={() => void openTrial('image')} disabled={!!pending || trialPending} icon={<Play className="h-3.5 w-3.5" />}>图片试用</Button>}
          <Button size="sm" variant="accent" onClick={() => void runAction('confirm')} loading={pending === 'confirm'} disabled={!!pending || trialPending} icon={<Check className="h-3.5 w-3.5" />}>{isUpdate ? '更新现有草稿' : '创建草稿'}</Button>
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)} disabled={!!pending} icon={<Pencil className="h-3.5 w-3.5" />}>编辑</Button>
          <Button size="sm" variant="secondary" onClick={() => void runAction('cancel')} loading={pending === 'cancel'} disabled={!!pending} icon={<X className="h-3.5 w-3.5" />}>取消</Button>
        </>}
        {awaiting && editing && <Button size="sm" variant="secondary" onClick={() => { setEditing(false); setForm(initialEdit(changeSet)); }} disabled={!!pending}>返回预览</Button>}
        {applied && <span className="text-xs text-text-secondary">草稿编号：{changeSet.resource_id}</span>}
      </footer>
    </section>
  );
}

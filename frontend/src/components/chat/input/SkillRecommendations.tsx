import { useEffect, useState } from 'react';
import { ArrowLeft, Check, Images, Info, SlidersHorizontal, X } from 'lucide-react';
import {
  getSkillRecommendations, sendSkillRecommendationFeedback, skillVersion, supportsSkillMode, type SkillTaskMode,
  type SkillBinding, type SkillFileType, type SkillRecommendation, type SkillRecommendationBatch, type SkillSummary,
} from '../../../services/skills';

const types: [SkillFileType, string][] = [
  ['image', '图片'], ['pdf', 'PDF'], ['docx', 'Word'], ['xlsx', 'Excel'], ['csv', 'CSV'], ['pptx', 'PowerPoint'], ['text', '文本'],
];
function reasonText(reason: SkillRecommendation['reasons'][number]): string {
  switch (reason.code) {
    case 'organization': return '当前组织可用';
    case 'domain': return reason.values[0] === 'erp' ? '适用于 ERP 业务' : '适用于通用任务';
    case 'execution_mode': return '适用于当前执行场景';
    case 'task_mode': return '明确支持当前任务模式';
    case 'tools': return '相关工具当前可用';
    case 'file_type': return `匹配所选文件类型：${reason.values.map(v => types.find(([key]) => key === v)?.[1] ?? v).join('、')}`;
    case 'session_binding': return '当前会话已固定此版本';
  }
}

/** One catalog with optional recommendation annotations; summaries remain catalog-owned. */
export default function SkillRecommendations({ conversationId, skills, disabled, onSelect, permissionMode,
  enabled = true, taskMode = 'smart', selected = null, bindings = [], onClose }: {
  conversationId: string; skills: SkillSummary[]; disabled: boolean;
  onSelect: (skill: SkillSummary) => void; permissionMode: 'auto' | 'ask' | 'plan';
  taskMode?: SkillTaskMode; enabled?: boolean; selected?: SkillSummary | null; bindings?: SkillBinding[]; onClose?: () => void;
}) {
  const [fileType, setFileType] = useState<SkillFileType | ''>('');
  const [view, setView] = useState<'list' | 'detail' | 'files'>('list');
  const [detailId, setDetailId] = useState<string | null>(null);
  const requestKey = JSON.stringify([conversationId, fileType, permissionMode, enabled, taskMode]);
  const [loaded, setLoaded] = useState<{ key: string; batch: SkillRecommendationBatch } | null>(null);
  const batch = loaded?.key === requestKey ? loaded.batch : null;
  const [dismissed, setDismissed] = useState<{ key: string; ids: string[] } | null>(null);
  const [feedbackFailed, setFeedbackFailed] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    void getSkillRecommendations(conversationId, fileType ? [fileType] : [], permissionMode, ...(taskMode !== 'smart' ? [taskMode] as const : [])).then(result => {
      if (current) setLoaded({ key: requestKey, batch: result });
    }).catch(() => {
      if (current) setLoaded({ key: requestKey, batch: { status: 'unavailable', recommendation_id: null, candidates: [] } });
    });
    return () => { current = false; };
  }, [conversationId, fileType, permissionMode, requestKey, enabled, taskMode]);

  const candidates = enabled && batch?.status === 'ready' ? batch.candidates.filter(c =>
    !(dismissed?.key === requestKey && dismissed.ids.includes(c.skill_id))
    && skills.some(s => s.skill_id === c.skill_id && s.revision === c.revision)).slice(0, 3) : [];
  const catalog = [...new Map(skills.filter(s => supportsSkillMode(s, taskMode)).map(skill => [skill.skill_id, skill])).values()];
  const ordered = [...catalog].sort((a, b) => Number(candidates.some(c => c.skill_id === b.skill_id))
    - Number(candidates.some(c => c.skill_id === a.skill_id)));
  const detail = catalog.find(skill => skill.skill_id === detailId);
  const candidate = detail && candidates.find(c => c.skill_id === detail.skill_id && c.revision === detail.revision);
  const detailBinding = detail && bindings.find(b => b.skill_id === detail.skill_id);
  const filesEnabled = enabled && batch?.status !== 'disabled';
  const activeView = view === 'files' && !filesEnabled ? 'list' : view;
  const feedback = (skill: SkillRecommendation, value: 'selected' | 'not_relevant') => {
    if (batch?.recommendation_id) void sendSkillRecommendationFeedback(
      conversationId, batch.recommendation_id, skill, value,
    ).catch(() => setFeedbackFailed(requestKey));
  };
  const choose = (skill: SkillSummary) => {
    if (disabled || bindings.some(b => b.skill_id === skill.skill_id)) return;
    const recommendation = candidates.find(c => c.skill_id === skill.skill_id && c.revision === skill.revision);
    if (recommendation) feedback(recommendation, 'selected');
    onSelect(skill);
  };
  const iconClass = 'flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-tertiary hover:bg-hover hover:text-text-primary focus-visible:outline-2 focus-visible:outline-accent';
  return <section aria-label="可用 Skill">
    <div className="flex items-center gap-2 px-1.5 pb-1 text-sm">
      {activeView !== 'list' && <button type="button" className={iconClass} aria-label="返回 Skill 列表" onClick={() => setView('list')}><ArrowLeft className="h-4 w-4" /></button>}
      <h2 className="font-medium text-text-primary">{activeView === 'list' ? 'Skill' : activeView === 'detail' ? 'Skill 详情' : '文件类型'}</h2>
      {activeView === 'list' && <span className="text-xs text-text-tertiary">{catalog.length} 个可用</span>}
      <button type="button" className={`${iconClass} ml-auto`} aria-label="关闭 Skill 面板" onClick={onClose}><X className="h-4 w-4" /></button>
    </div>
    {activeView === 'list' && <>
      <div className="max-h-[min(320px,50vh)] overflow-y-auto">
        {ordered.length === 0 && <p role="status" className="p-4 text-sm text-text-tertiary">当前会话暂无可用 Skill</p>}
        {ordered.map(skill => {
          const binding = bindings.find(b => b.skill_id === skill.skill_id);
          const chosen = selected?.skill_id === skill.skill_id && selected.revision === skill.revision;
          return <div key={skill.skill_id} className={`flex items-start gap-1 rounded-lg p-1 hover:bg-hover ${chosen || binding ? 'bg-hover' : ''}`}>
            <button type="button" disabled={disabled || !!binding} aria-pressed={chosen || !!binding}
              aria-label={`选择 Skill：${skill.name}`} onClick={() => choose(skill)}
              className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-1 py-2 text-left focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-60">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center self-start rounded-lg bg-surface text-text-tertiary"><Images className="h-4 w-4" aria-hidden="true" /></span>
              <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-text-primary" title={skill.name}>{skill.name}</span><span className="mt-1 block truncate text-xs text-text-tertiary" title={skill.description}>{skill.description}</span></span>
              <span className="shrink-0 text-xs text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)]">{binding ? '已固定' : chosen ? '已选' : '选择'}</span>
            </button>
            <button type="button" aria-label={`Skill 详情：${skill.name}`} onClick={() => { setDetailId(skill.skill_id); setView('detail'); }}
              className={`${iconClass} mt-2`}><Info className="h-4 w-4" /></button>
          </div>;
        })}
      </div>
      <div className="mx-1 mt-1 flex flex-wrap items-center justify-between gap-2 border-t border-border-default px-0.5 pb-0.5 pt-2 text-xs text-text-tertiary">
        <span>{selected ? '已选择 · 仅用于本条消息' : bindings.length ? `已固定 ${bindings.length} 个 · 当前会话` : '选择后仅用于本条消息'}</span>
        {filesEnabled && <button type="button" onClick={() => setView('files')} className="flex items-center gap-1 rounded-md p-1 hover:bg-hover hover:text-text-primary"><SlidersHorizontal className="h-3.5 w-3.5" />{fileType ? types.find(([key]) => key === fileType)?.[1] : '文件类型'}</button>}
      </div>
    </>}
    {activeView === 'detail' && detail && <div className="max-h-[min(360px,55vh)] overflow-y-auto px-2 pb-2 pt-1 text-xs leading-5 text-text-secondary">
      <h3 className="mb-2 break-words text-sm font-medium text-text-primary">{detail.name}</h3>
      <p className="break-words">{detail.description}</p>
      <dl className="my-3 grid grid-cols-[48px_1fr] gap-2"><dt className="text-text-tertiary">版本</dt><dd>{skillVersion(detail.revision)} · {detail.source === 'org' ? '组织 Skill' : '平台 Skill'}</dd>
        {candidate && <><dt className="text-text-tertiary">依据</dt><dd>{candidate.reasons.map(reasonText).join('；')}</dd></>}
      </dl>
      {detailBinding && <p className="mb-3">当前会话已固定 {skillVersion(detailBinding.revision)}，可在输入框标签旁切换范围或移除。</p>}
      {candidate && <button type="button" disabled={disabled} aria-label={`不相关：${detail.name}`} className="mb-3 text-text-tertiary hover:text-text-primary disabled:opacity-40" onClick={() => {
        feedback(candidate, 'not_relevant');
        setDismissed(previous => ({ key: requestKey, ids: [...(previous?.key === requestKey ? previous.ids : []), detail.skill_id] }));
      }}>这条建议不相关</button>}
      <button type="button" disabled={disabled || !!detailBinding} onClick={() => choose(detail)}
        className="flex w-full items-center justify-center gap-1 rounded-lg bg-accent-light px-3 py-2 text-sm text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)] hover:bg-accent/15 disabled:opacity-50">
        {detailBinding && <Check className="h-4 w-4" />}{detailBinding ? '已固定到当前会话' : '选择，用于本条消息'}
      </button>
    </div>}
    {activeView === 'files' && <div className="px-2 pb-2 pt-1 text-xs text-text-tertiary">
      <p className="my-2">按你选择的文件类型调整建议顺序。</p>
      <label className="my-3 flex items-center justify-between gap-2">处理的文件类型
        <select aria-label="推荐文件类型" value={fileType} disabled={disabled}
          onChange={e => { setLoaded(null); setDismissed(null); setFeedbackFailed(null); setFileType(e.target.value as SkillFileType | ''); }}
          className="rounded-lg border border-border-default bg-surface px-2 py-1 text-sm text-text-primary">
          <option value="">不限</option>{types.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <p role="status" className="mb-3">{!batch ? '正在获取建议…' : batch.status === 'unavailable' ? '建议暂不可用，仍可自行选择 Skill。' : candidates.length === 0 ? '暂无匹配建议，仍可自行选择。' : '已更新建议。'}</p>
      <button type="button" onClick={() => setView('list')} className="w-full rounded-lg bg-accent-light px-3 py-2 text-sm text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)]">完成</button>
    </div>}
    {feedbackFailed === requestKey && <p role="status" className="px-2 pb-2 text-xs text-text-tertiary">反馈未保存，不影响选择。</p>}
  </section>;
}

import { useEffect, useState } from 'react';
import {
  getSkillRecommendations, sendSkillRecommendationFeedback, skillVersion,
  type SkillFileType, type SkillRecommendation, type SkillRecommendationBatch, type SkillSummary,
} from '../../../services/skills';

const types: [SkillFileType, string][] = [
  ['pdf', 'PDF'], ['docx', 'Word'], ['xlsx', 'Excel'], ['csv', 'CSV'], ['pptx', 'PowerPoint'], ['image', '图片'], ['text', '文本'],
];
function reasonText(reason: SkillRecommendation['reasons'][number]): string {
  switch (reason.code) {
    case 'organization': return '当前组织可用';
    case 'domain': return reason.values[0] === 'erp' ? '适用于 ERP 业务' : '适用于通用任务';
    case 'execution_mode': return '适用于当前执行场景';
    case 'tools': return '相关工具当前可用';
    case 'file_type': return `匹配所选文件类型：${reason.values.join('、')}`;
    case 'session_binding': return '当前会话已固定此版本';
  }
}

export default function SkillRecommendations({ conversationId, skills, disabled, onSelect, permissionMode }: {
  conversationId: string; skills: SkillSummary[]; disabled: boolean;
  onSelect: (skill: SkillSummary) => void; permissionMode: 'auto' | 'ask' | 'plan';
}) {
  const [fileType, setFileType] = useState<SkillFileType | ''>('');
  const requestKey = JSON.stringify([conversationId, fileType, permissionMode]);
  const [loaded, setLoaded] = useState<{ key: string; batch: SkillRecommendationBatch } | null>(null);
  const batch = loaded?.key === requestKey ? loaded.batch : null;
  const [dismissed, setDismissed] = useState<{ key: string; ids: string[] } | null>(null);
  const [feedbackFailed, setFeedbackFailed] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    void getSkillRecommendations(conversationId, fileType ? [fileType] : [], permissionMode).then(result => {
      if (current) setLoaded({ key: requestKey, batch: result });
    }).catch(() => {
      if (current) setLoaded({ key: requestKey, batch: { status: 'unavailable', recommendation_id: null, candidates: [] } });
    });
    return () => { current = false; };
  }, [conversationId, fileType, permissionMode, requestKey]);

  if (batch?.status === 'disabled') return null;
  // A bad/stale recommendation never adds a new selectable identity to the catalog.
  const candidates = (batch?.candidates ?? []).filter(c => !(dismissed?.key === requestKey && dismissed.ids.includes(c.skill_id))
    && skills.some(s => s.skill_id === c.skill_id && s.revision === c.revision)).slice(0, 3);
  const feedback = (skill: SkillRecommendation, value: 'selected' | 'not_relevant') => {
    if (batch?.recommendation_id) void sendSkillRecommendationFeedback(
      conversationId, batch.recommendation_id, skill, value,
    ).catch(() => setFeedbackFailed(requestKey));
  };
  return <section aria-label="Skill 建议" className="mb-2 max-h-64 overflow-y-auto border-b border-border-default pb-2">
    <div className="px-2 py-1 text-xs text-text-secondary">建议使用 · 选择后随本条消息启用</div>
    <label className="block px-2 py-1 text-xs text-text-tertiary">处理的文件类型
      <select aria-label="推荐文件类型" value={fileType} disabled={disabled}
        onChange={e => { setLoaded(null); setDismissed(null); setFeedbackFailed(null); setFileType(e.target.value as SkillFileType | ''); }}
        className="ml-2 rounded bg-surface text-text-primary">
        <option value="">未指定</option>{types.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
    {!batch ? <p role="status" className="p-2 text-xs text-text-tertiary">正在获取建议…</p>
      : batch.status === 'unavailable' ? <p role="status" className="p-2 text-xs text-text-tertiary">建议暂不可用，可从全部 Skill 中选择。</p>
      : candidates.length === 0 ? <p className="p-2 text-xs text-text-tertiary">暂无建议，可自行选择 Skill。</p>
      : candidates.map(skill => <div key={skill.skill_id} className="rounded-lg p-2 hover:bg-hover">
        <button type="button" disabled={disabled} className="w-full text-left"
          aria-label={`选择建议：${skill.name}`} onClick={() => {
            const visible = skills.find(s => s.skill_id === skill.skill_id && s.revision === skill.revision);
            if (!visible) return;
            feedback(skill, 'selected');
            onSelect(visible);
          }}>
          <div className="text-sm text-text-primary break-words">{skill.name} · {skillVersion(skill.revision)}</div>
          <div className="mt-1 text-xs text-text-tertiary">{skill.reasons.map(reasonText).join('；')}</div>
        </button>
        <button type="button" disabled={disabled} aria-label={`不相关：${skill.name}`}
          className="mt-1 text-xs text-text-tertiary hover:text-text-primary" onClick={() => {
            feedback(skill, 'not_relevant');
            setDismissed(previous => ({ key: requestKey, ids: [...(previous?.key === requestKey ? previous.ids : []), skill.skill_id] }));
          }}>不相关</button>
      </div>)}
    {feedbackFailed === requestKey && <p role="status" className="px-2 text-xs text-text-tertiary">反馈未保存，不影响选择。</p>}
  </section>;
}

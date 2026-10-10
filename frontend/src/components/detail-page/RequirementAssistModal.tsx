import { Plus, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import type { RequirementAssistResult } from '../../types/ecomRequirement';
import Modal from '../common/Modal';
import { Button } from '../ui/Button';

interface Props {
  isOpen: boolean; isLoading: boolean; draft: RequirementAssistResult | null;
  brief: string; error: string | null; validationError: string | null;
  supplement: string; answers: Record<string, string>; skippedQuestions: string[];
  onClose: () => void; onDraftChange: (patch: Partial<RequirementAssistResult>) => void;
  onSupplementChange: (text: string) => void; onAnswer: (question: string, value: string) => void;
  onToggleSkip: (question: string) => void; onUpdate: () => void; onConfirm: (brief: string) => void;
}
const inputClass = 'w-full rounded-[var(--c-input-radius)] border border-[var(--c-input-border)] bg-[var(--c-input-bg)] px-3 py-2 text-sm leading-6 text-[var(--c-input-fg)] focus:outline-none focus:border-[var(--c-input-border-focus)] disabled:opacity-60';
const sectionClass = 'space-y-2 rounded-[var(--s-radius-card)] border border-[var(--s-border-subtle)] p-3';

export function RequirementAssistModal(props: Props) {
  const { draft, isLoading, onDraftChange } = props;
  return <Modal isOpen={props.isOpen} onClose={props.onClose} title="AI 帮写 · 产品资料与创作要求" maxWidth="max-w-4xl"
    footer={<div className="space-y-2">
      {props.validationError && <p role="alert" className="text-sm text-[var(--s-error)]">{props.validationError}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" icon={<RefreshCw className="h-4 w-4"/>} loading={isLoading} disabled={isLoading} onClick={props.onUpdate}>{draft?'更新草稿':'重新尝试'}</Button>
        <Button className="ml-auto" icon={<Sparkles className="h-4 w-4"/>} disabled={isLoading||!draft||!props.brief.trim()||Boolean(props.validationError)} onClick={()=>props.onConfirm(props.brief)}>插入到输入框</Button>
      </div>
    </div>}>
    <div>
      <p className="mb-3 text-sm text-[var(--s-text-tertiary)]">Kimi K3 会结合图片和文字拆解产品资料，提出需要补充的问题。请核验卖点、补充细节与风格要求；暂不知道的可以跳过。</p>
      <div className="space-y-3">
        {props.error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{props.error}</p>}
        {!draft && isLoading && <div className="flex min-h-[280px] flex-col items-center justify-center gap-3">
          <RefreshCw className="h-6 w-6 animate-spin text-[var(--s-accent)]"/>
          <p>正在分析图片和文字…</p><p className="text-sm text-[var(--s-text-tertiary)]">拆解已有资料，整理可向您补充的问题</p>
        </div>}
        {!draft && !isLoading && <p className="py-16 text-center text-sm text-[var(--s-text-secondary)]">暂时无法生成草稿，请重试。</p>}
        {draft && <>
          <section className={sectionClass}>
            <label htmlFor="assist-product" className="block text-sm font-semibold">产品细节与规格</label>
            <textarea id="assist-product" rows={4} maxLength={3000} disabled={isLoading} value={draft.product_description} onChange={event=>onDraftChange({product_description:event.target.value})} className={inputClass}/>
          </section>
          <div className="grid gap-3 md:grid-cols-2">
            <section className={sectionClass}>
              <h3 className="text-sm font-semibold">卖点拆分</h3>
              {draft.selling_points.map((point,index)=><div key={index} className="space-y-1.5 rounded-lg bg-[var(--s-surface-subtle)] p-2.5">
                <div className="flex items-center justify-between text-xs text-[var(--s-text-tertiary)]">
                  <span>{point.benefit_basis==='inferred'?'AI 建议 · 请核验':'基于已知信息'}</span>
                  <button type="button" aria-label={'删除卖点'+(index+1)} disabled={isLoading} onClick={()=>onDraftChange({selling_points:draft.selling_points.filter((_,i)=>i!==index)})}><Trash2 className="h-3.5 w-3.5"/></button>
                </div>
                <input aria-label={'卖点'+(index+1)+'特点'} maxLength={500} disabled={isLoading} value={point.feature} onChange={event=>onDraftChange({selling_points:draft.selling_points.map((item,i)=>i===index?{...item,feature:event.target.value}:item)})} className={inputClass}/>
                <textarea aria-label={'卖点'+(index+1)+'价值'} rows={2} maxLength={500} disabled={isLoading} value={point.benefit} onChange={event=>onDraftChange({selling_points:draft.selling_points.map((item,i)=>i===index?{...item,benefit:event.target.value}:item)})} className={inputClass}/>
              </div>)}
              <Button variant="ghost" size="sm" icon={<Plus className="h-4 w-4"/>} disabled={isLoading||draft.selling_points.length>=12} onClick={()=>onDraftChange({selling_points:[...draft.selling_points,{feature:'',benefit:'',benefit_basis:'inferred'}]})}>补充卖点</Button>
            </section>
            <section className={sectionClass}>
              <h3 className="text-sm font-semibold">背景与风格要求</h3>
              {draft.creative_requirements.map((item,index)=><div key={index} className="space-y-1.5">
                <div className="flex items-center gap-2 text-xs text-[var(--s-text-tertiary)]">
                  <label htmlFor={'assist-direction-'+index} className="flex-1">{item.topic} · {item.basis==='explicit'?'用户要求':'AI 建议'}</label>
                  <button type="button" aria-label={'删除设计要求'+(index+1)} disabled={isLoading} onClick={()=>onDraftChange({creative_requirements:draft.creative_requirements.filter((_,i)=>i!==index)})}><Trash2 className="h-3.5 w-3.5"/></button>
                </div>
                <textarea id={'assist-direction-'+index} rows={2} maxLength={1000} disabled={isLoading} value={item.text} onChange={event=>onDraftChange({creative_requirements:draft.creative_requirements.map((row,i)=>i===index?{...row,text:event.target.value}:row)})} className={inputClass}/>
              </div>)}
              <Button variant="ghost" size="sm" icon={<Plus className="h-4 w-4"/>} disabled={isLoading||draft.creative_requirements.length>=20} onClick={()=>onDraftChange({creative_requirements:[...draft.creative_requirements,{topic:'补充要求',text:'',basis:'explicit'}]})}>补充设计要求</Button>
            </section>
          </div>
          <section className={sectionClass}>
            <h3 className="text-sm font-semibold">补充信息（可选）</h3>
            {props.skippedQuestions.filter(question=>!draft.supplement_questions.some(item=>item.question===question)).map(question=><div key={question} className="flex items-center justify-between gap-2 text-xs text-[var(--s-text-tertiary)]">
              <span>已跳过：{question}（之后仍可在下方补充）</span>
            </div>)}
            {draft.supplement_questions.map((item,index)=>{
              const skipped=props.skippedQuestions.includes(item.question);
              return <div key={item.question} className="space-y-1">
                <div className="flex items-start justify-between gap-2">
                  <label htmlFor={'assist-answer-'+index} className="text-sm">{item.question}</label>
                  <button type="button" disabled={isLoading} aria-pressed={skipped} onClick={()=>props.onToggleSkip(item.question)} className="shrink-0 text-xs text-[var(--s-accent)]">{skipped?'恢复填写':'暂不知道 / 跳过'}</button>
                </div>
                <p className="text-xs text-[var(--s-text-tertiary)]">{item.why}</p>
                {!skipped && <input id={'assist-answer-'+index} maxLength={1000} disabled={isLoading} value={props.answers[item.question]??''} onChange={event=>props.onAnswer(item.question,event.target.value)} placeholder="有资料就补充，也可以直接插入草稿" className={inputClass}/>}
              </div>;
            })}
            <label htmlFor="assist-supplement" className="block pt-1 text-sm">其他补充或修改方向</label>
            <textarea id="assist-supplement" rows={3} maxLength={4000} disabled={isLoading} value={props.supplement} onChange={event=>props.onSupplementChange(event.target.value)} placeholder="例如补充尺寸、修正卖点，或调整背景、场景和整体风格…" className={inputClass}/>
            <p className="text-xs text-[var(--s-text-tertiary)]">点击“插入到输入框”会一并带入产品细节、卖点、风格要求和补充回答；需要 AI 再整理时，点击“更新草稿”。</p>
          </section>
        </>}
      </div>
    </div>
  </Modal>;
}

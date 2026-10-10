import { useRef, useState } from 'react';
import { FolderOpen, Paperclip, Sparkles, X } from 'lucide-react';
import type { DetailGenerationForm, DetailLocalImage, PromptModelOption } from '../../types/detailPage';
import { cn } from '../../utils/cn';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';
import { WorkspaceImagePicker } from './WorkspaceImagePicker';

interface Props {
  form: DetailGenerationForm; hasProductImage: boolean; disabled?: boolean; requirementDisabled?: boolean; requirementAssistDisabled?: boolean;
  models?: PromptModelOption[]; ratios?: string[]; images?: DetailLocalImage[];
  onAdd?: (files: File[])=>void; onWorkspaceAdd?: (paths: string[])=>void; onRemove?: (id: string)=>void;
  onChange: (patch:Partial<DetailGenerationForm>)=>void; onRequirementAssist:()=>void; onAnalyze:()=>void;
}
const platforms=[{value:'auto',label:'智能匹配'},{value:'taobao',label:'淘宝'},{value:'tmall',label:'天猫'},{value:'jd',label:'京东'},{value:'pdd',label:'拼多多'}] as const;
const languages=[{value:'zh-CN',label:'中文（简体）'},{value:'none',label:'无文字'}] as const;
const qualities=[{value:'1k',label:'1K 标准'},{value:'2k',label:'2K 高清'},{value:'4k',label:'4K 超清'}] as const;
const counts=Array.from({length:15},(_,index)=>({value:String(index+1),label:`${index+1}张`}));
const defaultModels=[{value:'kimi-k3',label:'Kimi K3'},{value:'gemini-3.8-flash',label:'Gemini 3.8 Flash'}] as const;
export function GenerationSettings({form,hasProductImage,disabled=false,requirementDisabled=disabled,requirementAssistDisabled=false,models,ratios=['1:1','3:4','4:5','16:9'],images=[],onAdd,onWorkspaceAdd,onRemove,onChange,onRequirementAssist,onAnalyze}:Props){
  const inputRef = useRef<HTMLInputElement>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const requirementLabel = form.contentType === 'default' ? '产品信息与创作要求' : form.contentType === 'main_image' ? '主图要求' : '详情图要求';
  return <section className="flex h-full min-h-0 flex-col gap-2">
    <div className="grid shrink-0 grid-cols-3 gap-1 rounded-[var(--s-radius-control)] bg-[var(--s-surface-secondary)] p-1" aria-label="生成类型">
      {([['default','默认'],['main_image','主图'],['detail_page','详情图']] as const).map(([value,label])=>
        <button type="button" key={value} disabled={disabled} aria-pressed={form.contentType===value} onClick={()=>onChange({contentType:value})}
          className={cn('rounded-[var(--s-radius-control)] px-3 py-1.5 text-sm font-medium disabled:opacity-50',form.contentType===value?'bg-[var(--s-surface-card)] text-[var(--s-accent)] shadow-sm':'text-[var(--s-text-secondary)]')}>{label}</button>)}
    </div>
    <div className="grid shrink-0 grid-cols-2 gap-x-2 gap-y-1">
      <label className="min-w-0 text-xs text-[var(--s-text-secondary)]">目标平台<Select size="compact" ariaLabel="目标平台" value={form.platform} options={platforms} disabled={disabled} onChange={platform=>onChange({platform})}/></label>
      <label className="min-w-0 text-xs text-[var(--s-text-secondary)]">目标语言<Select size="compact" ariaLabel="目标语言" value={form.language} options={languages} disabled={disabled} onChange={language=>onChange({language})}/></label>
      <label className="min-w-0 text-xs text-[var(--s-text-secondary)]">尺寸比例<Select size="compact" ariaLabel="尺寸比例" value={form.aspectRatio} options={ratios.map(value=>({value,label:value}))} disabled={disabled} onChange={aspectRatio=>onChange({aspectRatio})}/></label>
      <label className="min-w-0 text-xs text-[var(--s-text-secondary)]">清晰度<Select size="compact" ariaLabel="清晰度" value={form.quality} options={qualities} disabled={disabled} onChange={quality=>onChange({quality})}/></label>
      <label className="min-w-0 text-xs text-[var(--s-text-secondary)]">
        <span className="flex h-5 items-center justify-between"><span>生成数量</span><span className="whitespace-nowrap text-[10px]">{form.contentType==='default'?'7张主图＋7张详情':'\u00a0'}</span></span>
        <Select size="compact" ariaLabel="生成数量" value={String(form.contentType==='default'?14:form.count)} options={form.contentType==='default'?[{value:'14',label:'14张'}]:counts} disabled={disabled} onChange={count=>onChange({count:Number(count)})}/>
      </label>
      <label className="min-w-0 text-xs text-[var(--s-text-secondary)]">提示词模型
        <Select size="compact" ariaLabel="提示词模型" value={form.promptModel??'kimi-k3'} options={models?.length?models.map(model=>({value:model.id,label:model.name+(model.available?'':'（暂不可用）')})):defaultModels}
          disabled={disabled} onChange={promptModel=>onChange({promptModel})}/>
      </label>
    </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[var(--c-input-border)] bg-[var(--c-input-bg)] focus-within:border-[var(--c-input-border-focus)]">
        {images.length > 0 && <div aria-label="已上传图片" className="grid shrink-0 grid-cols-5 gap-1.5 px-3 pt-3">
          {images.map((image) => <div key={image.id} className="relative aspect-square min-w-0 overflow-hidden rounded-lg border border-[var(--s-border-default)]">
            {image.previewUrl ? <img src={image.previewUrl} alt={image.name || '产品图'} className="h-full w-full object-contain" onError={(event) => { if (image.originalUrl && event.currentTarget.src !== image.originalUrl) event.currentTarget.src = image.originalUrl; }} /> : <div className="flex h-full items-center justify-center text-xs">原图缺失</div>}
            {!disabled && <button type="button" aria-label={`删除 ${image.name || '图片'}`} onClick={() => onRemove?.(image.id)} className="absolute right-0 top-0 rounded-full bg-black/60 p-0.5 text-white"><X className="h-3.5 w-3.5" /></button>}
            {image.status !== 'ready' && <span className="absolute inset-x-0 bottom-0 bg-black/60 text-center text-[10px] text-white">{image.status === 'failed' ? '上传失败' : image.status === 'missing' ? '原图缺失' : '上传中'}</span>}
          </div>)}
        </div>}
        <label htmlFor="detail-requirement" className="sr-only">{requirementLabel}</label>
        <textarea id="detail-requirement" disabled={requirementDisabled} maxLength={10000} value={form.requirement} onChange={(event) => onChange({ requirement: event.target.value })} placeholder="上传产品图片，并描述产品名称、核心卖点、规格和设计要求…" className="min-h-0 w-full flex-1 resize-none overflow-y-auto overscroll-contain border-0 bg-transparent p-3 text-sm leading-6 text-[var(--s-text-primary)] placeholder:text-[var(--s-text-tertiary)] focus:outline-none disabled:opacity-50" />
        <div className="flex shrink-0 items-center gap-0.5 px-1.5 pb-1.5">
          <input ref={inputRef} type="file" aria-label="上传产品图" accept="image/jpeg,image/png,image/webp" multiple disabled={disabled} className="hidden" onChange={(event) => { const files = Array.from(event.target.files ?? []); if (files.length) onAdd?.(files); event.target.value = ''; }} />
          <Button variant="ghost" size="sm" className="shrink-0 gap-1 px-1.5 text-xs" icon={<Paperclip className="h-4 w-4" />} disabled={disabled || images.length >= 9} onClick={() => inputRef.current?.click()}>上传图片</Button>
          <Button variant="ghost" size="sm" className="shrink-0 gap-1 px-1.5 text-xs" icon={<FolderOpen className="h-4 w-4" />} disabled={disabled || images.length >= 9} onClick={() => setPickerOpen(true)}>工作区</Button>
          <Button variant="ghost" size="sm" className="shrink-0 gap-1 px-1.5 text-xs" icon={<Sparkles className="h-4 w-4" />} disabled={disabled || requirementAssistDisabled} onClick={onRequirementAssist}>AI 帮写</Button>
          <span className="ml-auto shrink-0 px-1 text-xs text-[var(--s-text-tertiary)]">{images.length}/9</span>
        </div>
      </div>
    <Button fullWidth size="lg" className="shrink-0" disabled={disabled||!hasProductImage} onClick={onAnalyze}>开始生成</Button>
    <WorkspaceImagePicker open={pickerOpen} remaining={9-images.length} onClose={()=>setPickerOpen(false)} onSelect={paths=>onWorkspaceAdd?.(paths)}/>
  </section>;
}

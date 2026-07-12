import { Sparkles } from 'lucide-react';
import type { DetailGenerationForm } from '../../types/detailPage';
import { cn } from '../../utils/cn';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';

interface GenerationSettingsProps {
  form: DetailGenerationForm;
  hasProductImage: boolean;
  disabled?: boolean;
  onChange: (patch: Partial<DetailGenerationForm>) => void;
  onAnalyze: () => void;
}

const selectClass = 'w-full px-3 py-2 text-sm rounded-[var(--c-input-radius)] bg-[var(--c-input-bg)] text-[var(--c-input-fg)] border border-[var(--c-input-border)] focus:outline-none focus:border-[var(--c-input-border-focus)] disabled:opacity-50';
const platformOptions = [{ value: 'auto', label: '智能匹配' }, { value: 'taobao', label: '淘宝' }, { value: 'tmall', label: '天猫' }, { value: 'jd', label: '京东' }, { value: 'pdd', label: '拼多多' }] as const;
const languageOptions = [{ value: 'zh-CN', label: '中文（简体）' }, { value: 'none', label: '无文字' }] as const;
const ratioOptions = ['1:1', '3:4', '4:5', '16:9'].map((value) => ({ value, label: value }));
const qualityOptions = [{ value: '1k', label: '1K 标准' }, { value: '2k', label: '2K 高清' }, { value: '4k', label: '4K 超清' }] as const;
const countOptions = Array.from({ length: 9 }, (_, index) => ({ value: String(index + 1), label: `${index + 1} 张` }));

export function GenerationSettings({ form, hasProductImage, disabled = false, onChange, onAnalyze }: GenerationSettingsProps) {
  const requirementLabel = form.contentType === 'main_image' ? '主图要求' : '详情图要求';

  return (
    <section className="mt-6 pt-6 border-t border-[var(--s-border-subtle)] space-y-5">
      <div className="grid grid-cols-2 gap-2" aria-label="生成类型">
        {([['main_image', '主图'], ['detail_page', '详情图']] as const).map(([value, label]) => (
          <button key={value} type="button" disabled={disabled} aria-pressed={form.contentType === value} onClick={() => onChange({ contentType: value })} className={cn('px-3 py-2 rounded-[var(--s-radius-control)] border text-sm font-medium disabled:opacity-50', form.contentType === value ? 'bg-[var(--c-button-primary-bg)] text-[var(--c-button-primary-fg)] border-transparent' : 'bg-[var(--s-surface-card)] text-[var(--s-text-secondary)] border-[var(--s-border-default)]')}>
            {label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="text-sm font-medium text-[var(--s-text-secondary)]">目标平台<Select ariaLabel="目标平台" disabled={disabled} value={form.platform} options={platformOptions} onChange={(platform) => onChange({ platform })} /></label>
        <label className="text-sm font-medium text-[var(--s-text-secondary)]">目标语言<Select ariaLabel="目标语言" disabled={disabled} value={form.language} options={languageOptions} onChange={(language) => onChange({ language })} /></label>
        <label className="text-sm font-medium text-[var(--s-text-secondary)]">尺寸比例<Select ariaLabel="尺寸比例" disabled={disabled} value={form.aspectRatio} options={ratioOptions} onChange={(aspectRatio) => onChange({ aspectRatio })} /></label>
        <label className="text-sm font-medium text-[var(--s-text-secondary)]">清晰度<Select ariaLabel="清晰度" disabled={disabled} value={form.quality} options={qualityOptions} onChange={(quality) => onChange({ quality })} /></label>
        <label className="text-sm font-medium text-[var(--s-text-secondary)]">生成数量<Select ariaLabel="生成数量" disabled={disabled} value={String(form.count)} options={countOptions} onChange={(count) => onChange({ count: Number(count) })} /></label>
      </div>

      <div>
        <div className="flex items-center justify-between gap-3">
          <label htmlFor="detail-requirement" className="text-sm font-medium text-[var(--s-text-secondary)]">{requirementLabel}</label>
          <Button variant="ghost" size="sm" icon={<Sparkles className="w-4 h-4" />} disabled={disabled} onClick={() => onChange({ requirement: '突出产品核心卖点，画面简洁，适合目标电商平台展示。' })}>AI 帮写</Button>
        </div>
        <textarea id="detail-requirement" disabled={disabled} value={form.requirement} onChange={(event) => onChange({ requirement: event.target.value })} placeholder="建议输入：产品名称、核心卖点、目标人群、图片风格等" className={`${selectClass} min-h-24 resize-y`} />
      </div>

      <Button fullWidth size="lg" disabled={disabled || !hasProductImage} icon={<Sparkles className="w-4 h-4" />} onClick={onAnalyze}>分析产品</Button>
      {!hasProductImage && <p className="text-xs text-[var(--s-text-tertiary)]">请至少上传 1 张产品图</p>}
    </section>
  );
}

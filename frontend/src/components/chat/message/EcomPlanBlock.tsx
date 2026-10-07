/**
 * 电商图方案卡片（对话消息内渲染）
 *
 * 展示已保存的三阶段方案和完整执行提示词。
 * 类似 FormBlock 的交互式消息内容块。
 */

import { useEffect, useState } from 'react';
import { Check, Copy, Sparkles } from 'lucide-react';
import type { EcomPlanPart } from '../../../types/message';
import { MESSAGE_CONTENT_LAYOUT } from './messageContentLayout';
import { getEcommerceImagePlan, type EcommerceImagePlan } from '../../../services/ecommerceImagePlans';

interface EcomPlanBlockProps {
  plan: EcomPlanPart;
  onConfirm: (images: EcomPlanPart['images']) => void;
}

/**
 * 将用户编辑的文案同步到 prompt 中的中文引号位置。
 */
function syncTextToPrompt(prompt: string, newTitle: string, newSubtitle: string): string {
  const regex = /"([^"]*[\u4e00-\u9fff][^"]*)"/g;
  const matches = [...prompt.matchAll(regex)];
  if (!matches.length) return prompt;

  let result = prompt;
  if (matches.length >= 2 && newSubtitle) {
    const m = matches[1];
    result = result.slice(0, m.index!) + `"${newSubtitle}"` + result.slice(m.index! + m[0].length);
  }
  if (matches.length >= 1 && newTitle) {
    const m = matches[0];
    result = result.slice(0, m.index!) + `"${newTitle}"` + result.slice(m.index! + m[0].length);
  }
  return result;
}

export default function EcomPlanBlock({ plan, onConfirm }: EcomPlanBlockProps) {
  const [images, setImages] = useState(plan.images);
  const [confirmed, setConfirmed] = useState(false);
  const [saved, setSaved] = useState<EcommerceImagePlan | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    if (!plan.plan_id || !plan.revision) return;
    let active = true;
    getEcommerceImagePlan(plan.plan_id, plan.revision).then(value => {
      if (active) setSaved(value);
    }).catch(() => { if (active) setLoadError(true); });
    return () => { active = false; };
  }, [plan.plan_id, plan.revision]);

  const handleChange = (index: number, field: 'title' | 'subtitle', value: string) => {
    setImages(prev => {
      const updated = [...prev];
      const img = { ...updated[index], [field]: value };
      img.prompt = syncTextToPrompt(
        updated[index].prompt || '',
        field === 'title' ? value : img.title || '',
        field === 'subtitle' ? value : img.subtitle || '',
      );
      updated[index] = img;
      return updated;
    });
  };

  const handleConfirm = () => {
    if (plan.plan_id) return;
    setConfirmed(true);
    onConfirm(images);
  };

  return (
    <div className={`${MESSAGE_CONTENT_LAYOUT.fill} border border-border-primary rounded-xl bg-surface-primary overflow-hidden my-2`}>
      {plan.plan_id ? (
        <div className="px-4 py-3 space-y-2">
          <div className="text-sm font-medium text-text-primary">三阶段主图方案 · {plan.images.length} 张</div>
          {saved?.product_selling_points && <details><summary className="cursor-pointer text-sm text-text-secondary">查看商品事实与卖点</summary>
            <pre className="mt-2 whitespace-pre-wrap text-xs text-text-secondary">{JSON.stringify(saved.product_selling_points, null, 2)}</pre></details>}
          {saved?.visual_direction && <details><summary className="cursor-pointer text-sm text-text-secondary">查看整套视觉规范</summary>
            <pre className="mt-2 whitespace-pre-wrap text-xs text-text-secondary">{saved.visual_direction}</pre></details>}
          {loadError && <p className="text-xs text-text-tertiary">完整方案暂时无法读取，请稍后重试。</p>}
        </div>
      ) : (
      /* 顶部：旧版产品理解 + 视觉策略 */
      <div className="px-4 py-3 bg-surface-secondary border-b border-border-primary">
        <div className="space-y-1">
          {plan.product_insight && (
            <p className="text-sm text-text-primary">
              <span className="text-text-tertiary">📋 产品理解：</span>{plan.product_insight}
            </p>
          )}
          {plan.visual_strategy && (
            <p className="text-sm text-text-secondary">
              <span className="text-text-tertiary">🎨 视觉策略：</span>{plan.visual_strategy}
            </p>
          )}
        </div>
      </div>
      )}

      {/* 图片方案列表 */}
      <div className="divide-y divide-border-primary">
        {images.map((img, i) => (
          <div key={img.item_id || `${img.image_type}-${i}`} className="px-4 py-3">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-xs font-medium text-accent bg-accent/10 px-2 py-0.5 rounded-full">
                第{img.position || i + 1}张
              </span>
              <span className="text-sm font-medium text-text-primary">{img.name || img.role}</span>
              {img.image_type === 'white_bg' && (
                <span className="text-xs text-text-tertiary">（自动）</span>
              )}
            </div>
            <p className="text-xs text-text-tertiary mb-2">{img.purpose}</p>

            {plan.plan_id && saved?.images?.[i] && (
              <details className="mt-2 text-xs text-text-secondary">
                <summary className="cursor-pointer">查看本张完整方案与执行稿</summary>
                <div className="space-y-2 pt-2">
                  <pre className="whitespace-pre-wrap">{saved.images[i].scheme_markdown}</pre>
                  <details><summary className="cursor-pointer">完整正向提示词</summary><pre className="whitespace-pre-wrap pt-1">{saved.images[i].positive_prompt}</pre></details>
                  <details><summary className="cursor-pointer">负面提示词</summary><pre className="whitespace-pre-wrap pt-1">{saved.images[i].negative_prompt}</pre></details>
                  <button type="button" className="inline-flex items-center gap-1 rounded border border-border-primary px-2 py-1"
                    onClick={() => navigator.clipboard.writeText(saved.images[i].request_text)}><Copy className="h-3 w-3"/>复制完整执行稿</button>
                </div>
              </details>
            )}

            {/* 可编辑文案 */}
            {!plan.plan_id && img.image_type !== 'white_bg' && img.has_text && !confirmed && (
              <div className="flex gap-2">
                <input
                  type="text"
                  value={img.title || ''}
                  onChange={(e) => handleChange(i, 'title', e.target.value)}
                  placeholder="主标题"
                  className="flex-1 px-2.5 py-1.5 text-sm bg-surface-secondary rounded border border-border-primary focus:border-accent focus:outline-none text-text-primary"
                  maxLength={12}
                />
                <input
                  type="text"
                  value={img.subtitle || ''}
                  onChange={(e) => handleChange(i, 'subtitle', e.target.value)}
                  placeholder="副标题"
                  className="flex-1 px-2.5 py-1.5 text-sm bg-surface-secondary rounded border border-border-primary focus:border-accent focus:outline-none text-text-primary"
                  maxLength={15}
                />
              </div>
            )}
            {/* 确认后显示只读文案 */}
            {!plan.plan_id && img.has_text && confirmed && img.title && (
              <p className="text-sm text-text-secondary">
                {img.title}{img.subtitle ? ` · ${img.subtitle}` : ''}
              </p>
            )}
          </div>
        ))}
      </div>

      {/* 底部：积分预估 + 确认按钮 */}
      <div className="px-4 py-3 bg-surface-secondary border-t border-border-primary flex items-center justify-between">
        <span className="text-xs text-text-tertiary">
          {plan.cost_estimate
            ? `共 ${plan.cost_estimate.image_count} 张，预估 ${plan.cost_estimate.estimated_credits} 积分`
            : `共 ${images.length} 张`}
        </span>
        {plan.plan_id ? (
          <span className="text-sm text-text-secondary">方案已保存，主助手将按项提交图片生成</span>
        ) : confirmed ? (
          <span className="flex items-center gap-1 text-sm text-success">
            <Check className="w-4 h-4" /> 已确认，生成中...
          </span>
        ) : (
          <button
            type="button"
            onClick={handleConfirm}
            className="flex items-center gap-1.5 px-4 py-2 bg-accent text-white rounded-lg text-sm font-medium hover:bg-accent-dark transition-base"
          >
            <Sparkles className="w-4 h-4" />
            确认生成
          </button>
        )}
      </div>
    </div>
  );
}

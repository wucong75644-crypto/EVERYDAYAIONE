/**
 * 电商图方案卡片（对话消息内渲染）
 *
 * 展示千问 VL 策划的设计方案，用户可编辑文案后确认生成。
 * 类似 FormBlock 的交互式消息内容块。
 */

import { useState } from 'react';
import { Check, Sparkles } from 'lucide-react';
import type { EcomPlanPart } from '../../../types/message';

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

  const handleChange = (index: number, field: 'title' | 'subtitle', value: string) => {
    setImages(prev => {
      const updated = [...prev];
      const img = { ...updated[index], [field]: value };
      img.prompt = syncTextToPrompt(
        updated[index].prompt,
        field === 'title' ? value : img.title,
        field === 'subtitle' ? value : img.subtitle,
      );
      updated[index] = img;
      return updated;
    });
  };

  const handleConfirm = () => {
    setConfirmed(true);
    onConfirm(images);
  };

  return (
    <div className="border border-border-primary rounded-xl bg-surface-primary overflow-hidden my-2">
      {/* 顶部：产品理解 + 视觉策略 */}
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

      {/* 图片方案列表 */}
      <div className="divide-y divide-border-primary">
        {images.map((img, i) => (
          <div key={`${img.image_type}-${i}`} className="px-4 py-3">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-xs font-medium text-accent bg-accent/10 px-2 py-0.5 rounded-full">
                第{i + 1}张
              </span>
              <span className="text-sm font-medium text-text-primary">{img.role}</span>
              {img.image_type === 'white_bg' && (
                <span className="text-xs text-text-tertiary">（自动）</span>
              )}
            </div>
            <p className="text-xs text-text-tertiary mb-2">{img.purpose}</p>

            {/* 可编辑文案 */}
            {img.image_type !== 'white_bg' && img.has_text && !confirmed && (
              <div className="flex gap-2">
                <input
                  type="text"
                  value={img.title}
                  onChange={(e) => handleChange(i, 'title', e.target.value)}
                  placeholder="主标题"
                  className="flex-1 px-2.5 py-1.5 text-sm bg-surface-secondary rounded border border-border-primary focus:border-accent focus:outline-none text-text-primary"
                  maxLength={12}
                />
                <input
                  type="text"
                  value={img.subtitle}
                  onChange={(e) => handleChange(i, 'subtitle', e.target.value)}
                  placeholder="副标题"
                  className="flex-1 px-2.5 py-1.5 text-sm bg-surface-secondary rounded border border-border-primary focus:border-accent focus:outline-none text-text-primary"
                  maxLength={15}
                />
              </div>
            )}
            {/* 确认后显示只读文案 */}
            {img.has_text && confirmed && img.title && (
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
        {confirmed ? (
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

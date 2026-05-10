/**
 * 电商图方案卡片（v2）
 *
 * 展示千问 VL 输出的设计方案，渲染在输入区域上方。
 * 每张图一个卡片：角色 + 目的 + 可编辑的标题/副标题。
 * 用户编辑文案 → syncTextToPrompt → 实时更新 prompt。
 */

import { X } from 'lucide-react';

export interface ImageTask {
  role: string;
  purpose: string;
  title: string;
  subtitle: string;
  prompt: string;
  aspect_ratio: string;
  has_text: boolean;
  image_type: string;
}

interface EcomPlanCardsProps {
  productInsight: string;
  visualStrategy: string;
  images: ImageTask[];
  costEstimate: { estimated_credits: number; image_count: number } | null;
  onImageChange: (index: number, field: 'title' | 'subtitle', value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  isSubmitting: boolean;
}

/**
 * 将用户编辑的文案同步回 prompt 中的中文引号位置。
 * prompt 中中文用引号包裹（如 "一盒搞定"），按顺序替换。
 */
export function syncTextToPrompt(prompt: string, newTitle: string, newSubtitle: string): string {
  const regex = /"([^"]*[\u4e00-\u9fff][^"]*)"/g;
  const matches = [...prompt.matchAll(regex)];
  if (!matches.length) return prompt;

  let result = prompt;
  // 从后往前替换避免偏移
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

export function EcomPlanCards({
  productInsight,
  visualStrategy,
  images,
  costEstimate,
  onImageChange,
  onConfirm,
  onCancel,
  isSubmitting,
}: EcomPlanCardsProps) {
  return (
    <div className="border border-border-primary rounded-xl bg-surface-primary shadow-sm overflow-hidden">
      {/* 顶部：产品理解 + 视觉策略 */}
      <div className="px-4 py-3 bg-surface-secondary border-b border-border-primary">
        <div className="flex items-start justify-between">
          <div className="space-y-1 flex-1 min-w-0">
            {productInsight && (
              <p className="text-sm text-text-primary truncate">
                <span className="text-text-tertiary">产品理解：</span>{productInsight}
              </p>
            )}
            {visualStrategy && (
              <p className="text-sm text-text-secondary truncate">
                <span className="text-text-tertiary">视觉策略：</span>{visualStrategy}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="flex-shrink-0 ml-2 p-1 text-text-tertiary hover:text-text-primary rounded transition-colors"
            title="取消"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* 图片方案列表 */}
      <div className="max-h-[400px] overflow-y-auto divide-y divide-border-primary">
        {images.map((img, i) => (
          <div key={`${img.image_type}-${i}`} className="px-4 py-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-medium text-accent bg-accent/10 px-2 py-0.5 rounded-full">
                第{i + 1}张
              </span>
              <span className="text-sm font-medium text-text-primary">{img.role}</span>
              {img.image_type === 'white_bg' && (
                <span className="text-xs text-text-tertiary">（自动生成）</span>
              )}
            </div>

            <p className="text-xs text-text-tertiary mb-2">{img.purpose}</p>

            {/* 可编辑文案（白底图不可编辑） */}
            {img.image_type !== 'white_bg' && img.has_text && (
              <div className="space-y-1.5">
                <input
                  type="text"
                  value={img.title}
                  onChange={(e) => onImageChange(i, 'title', e.target.value)}
                  placeholder="主标题"
                  className="w-full px-2.5 py-1.5 text-sm bg-surface-secondary rounded border border-border-primary focus:border-accent focus:outline-none text-text-primary"
                  maxLength={12}
                />
                <input
                  type="text"
                  value={img.subtitle}
                  onChange={(e) => onImageChange(i, 'subtitle', e.target.value)}
                  placeholder="副标题（可选）"
                  className="w-full px-2.5 py-1.5 text-sm bg-surface-secondary rounded border border-border-primary focus:border-accent focus:outline-none text-text-primary"
                  maxLength={15}
                />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 底部：积分预估 + 确认按钮 */}
      <div className="px-4 py-3 bg-surface-secondary border-t border-border-primary flex items-center justify-between">
        <span className="text-xs text-text-tertiary">
          {costEstimate
            ? `共 ${costEstimate.image_count} 张，预估 ${costEstimate.estimated_credits} 积分`
            : `共 ${images.length} 张`}
        </span>
        <button
          type="button"
          disabled={isSubmitting}
          onClick={onConfirm}
          className="px-5 py-2 bg-accent text-white rounded-lg text-sm font-medium hover:bg-accent-dark transition-base disabled:opacity-50"
        >
          {isSubmitting ? '生成中...' : '确认生成'}
        </button>
      </div>
    </div>
  );
}

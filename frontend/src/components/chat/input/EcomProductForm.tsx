/**
 * 电商图产品信息表单（v2）
 *
 * 替代 ecom 模式下的纯文本输入框。
 * 必填：产品名称 + 目标平台
 * 选填：核心卖点 / 价格促销 / 目标用户 / 补充说明
 * 生成设置：主图/详情页开关 + 图片尺寸
 *
 * 产品图和风格参考图复用 InputArea 已有的图片上传逻辑，不在这里处理。
 */

import { useState } from 'react';
import { ChevronDown, ChevronUp, Loader2, Sparkles } from 'lucide-react';

const PLATFORMS = [
  { value: 'taobao', label: '淘宝/天猫' },
  { value: 'jd', label: '京东' },
  { value: 'pdd', label: '拼多多' },
  { value: 'douyin', label: '抖音' },
  { value: 'xiaohongshu', label: '小红书' },
  { value: 'ali1688', label: '1688' },
] as const;

const IMAGE_SIZES = [
  { value: '800x800', label: '800×800' },
  { value: '1024x1024', label: '1024×1024' },
  { value: '1024x1536', label: '1024×1536 竖版' },
] as const;

export interface EcomFormData {
  productName: string;
  platform: string;
  sellingPoints: string;
  priceInfo: string;
  targetUser: string;
  extraNotes: string;
  imageSize: string;
  generateDetail: boolean;
}

interface EcomProductFormProps {
  onSubmit: (data: EcomFormData) => void;
  isEnhancing: boolean;
  hasProductImages: boolean;
}

export function EcomProductForm({ onSubmit, isEnhancing, hasProductImages }: EcomProductFormProps) {
  const [productName, setProductName] = useState('');
  const [platform, setPlatform] = useState('taobao');
  const [sellingPoints, setSellingPoints] = useState('');
  const [priceInfo, setPriceInfo] = useState('');
  const [targetUser, setTargetUser] = useState('');
  const [extraNotes, setExtraNotes] = useState('');
  const [imageSize, setImageSize] = useState('800x800');
  const [generateDetail, setGenerateDetail] = useState(false);
  const [showMore, setShowMore] = useState(false);

  const canSubmit = productName.trim() && hasProductImages && !isEnhancing;

  const handleSubmit = () => {
    if (!canSubmit) return;
    onSubmit({
      productName: productName.trim(),
      platform,
      sellingPoints: sellingPoints.trim(),
      priceInfo: priceInfo.trim(),
      targetUser: targetUser.trim(),
      extraNotes: extraNotes.trim(),
      imageSize,
      generateDetail,
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && canSubmit) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="space-y-2.5 py-2">
      {/* 产品名称 + 平台（同行） */}
      <div className="flex gap-2">
        <input
          type="text"
          value={productName}
          onChange={(e) => setProductName(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="产品名称 *（如：56色拼豆收纳盒）"
          className="flex-1 px-3 py-2 text-sm bg-surface-secondary rounded-lg border border-border-primary focus:border-accent focus:outline-none text-text-primary placeholder:text-text-disabled"
          maxLength={100}
        />
        <select
          value={platform}
          onChange={(e) => setPlatform(e.target.value)}
          className="px-3 py-2 text-sm bg-surface-secondary rounded-lg border border-border-primary focus:border-accent focus:outline-none text-text-primary min-w-[120px]"
        >
          {PLATFORMS.map(p => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </select>
      </div>

      {/* 展开更多信息 */}
      <button
        type="button"
        onClick={() => setShowMore(!showMore)}
        className="flex items-center gap-1 text-xs text-text-tertiary hover:text-text-secondary transition-colors"
      >
        {showMore ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        {showMore ? '收起' : '填写更多信息（效果更好）'}
      </button>

      {showMore && (
        <div className="space-y-2">
          <input
            type="text"
            value={sellingPoints}
            onChange={(e) => setSellingPoints(e.target.value)}
            placeholder="核心卖点（如：4层大容量，装下200+瓶）"
            className="w-full px-3 py-2 text-sm bg-surface-secondary rounded-lg border border-border-primary focus:border-accent focus:outline-none text-text-primary placeholder:text-text-disabled"
            maxLength={500}
          />
          <input
            type="text"
            value={priceInfo}
            onChange={(e) => setPriceInfo(e.target.value)}
            placeholder="价格/促销（如：¥39.9 限时特惠）— 填了会生成促销图"
            className="w-full px-3 py-2 text-sm bg-surface-secondary rounded-lg border border-border-primary focus:border-accent focus:outline-none text-text-primary placeholder:text-text-disabled"
            maxLength={200}
          />
          <input
            type="text"
            value={targetUser}
            onChange={(e) => setTargetUser(e.target.value)}
            placeholder="目标用户（如：手工DIY爱好者、宝妈）"
            className="w-full px-3 py-2 text-sm bg-surface-secondary rounded-lg border border-border-primary focus:border-accent focus:outline-none text-text-primary placeholder:text-text-disabled"
            maxLength={200}
          />
          <input
            type="text"
            value={extraNotes}
            onChange={(e) => setExtraNotes(e.target.value)}
            placeholder="补充说明（其他你觉得重要的信息）"
            className="w-full px-3 py-2 text-sm bg-surface-secondary rounded-lg border border-border-primary focus:border-accent focus:outline-none text-text-primary placeholder:text-text-disabled"
            maxLength={500}
          />
        </div>
      )}

      {/* 生成设置 + 提交按钮（同行） */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 text-xs text-text-secondary">
          {/* 尺寸选择 */}
          <select
            value={imageSize}
            onChange={(e) => setImageSize(e.target.value)}
            className="px-2 py-1 bg-surface-secondary rounded border border-border-primary text-xs"
          >
            {IMAGE_SIZES.map(s => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
          {/* 详情页开关 */}
          <label className="flex items-center gap-1 cursor-pointer">
            <input
              type="checkbox"
              checked={generateDetail}
              onChange={(e) => setGenerateDetail(e.target.checked)}
              className="rounded border-border-primary"
            />
            <span>详情页</span>
          </label>
        </div>

        {/* 提交按钮 */}
        <button
          type="button"
          disabled={!canSubmit}
          onClick={handleSubmit}
          className="flex items-center gap-1.5 px-4 py-2 bg-accent text-white rounded-lg text-sm font-medium hover:bg-accent-dark transition-base disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isEnhancing ? (
            <><Loader2 className="w-4 h-4 animate-spin" />生成中...</>
          ) : (
            <><Sparkles className="w-4 h-4" />生成方案</>
          )}
        </button>
      </div>

      {/* 缺少产品图提示 */}
      {!hasProductImages && (
        <p className="text-xs text-warning">请先上传产品图片（点击左下角上传按钮）</p>
      )}
    </div>
  );
}

/**
 * 模型卡片
 *
 * 展示单个模型的信息、能力标签、费用和订阅按钮。
 */

import type { UnifiedModel } from '../../constants/models';

/** 能力图标映射 */
const CAPABILITY_TAGS: { key: string; check: (m: UnifiedModel) => boolean; label: string }[] = [
  { key: 'text', check: (m) => m.type === 'chat', label: '📝文本' },
  { key: 'vqa', check: (m) => !!m.capabilities.vqa, label: '🖼️图片' },
  { key: 'audio', check: (m) => !!m.capabilities.audioInput, label: '🎤音频' },
  { key: 'pdf', check: (m) => !!m.capabilities.pdfInput, label: '📄PDF' },
  { key: 'tools', check: (m) => !!m.capabilities.functionCalling, label: '🔧工具' },
  { key: 'textToImage', check: (m) => !!m.capabilities.textToImage, label: '🎨文生图' },
  { key: 'imageEdit', check: (m) => !!m.capabilities.imageEditing, label: '✏️编辑' },
  { key: 'textToVideo', check: (m) => !!m.capabilities.textToVideo, label: '🎬文生视频' },
  { key: 'imageToVideo', check: (m) => !!m.capabilities.imageToVideo, label: '🎞️图生视频' },
];

/** 获取费用显示文本 */
function getCreditsText(model: UnifiedModel): string {
  if (typeof model.credits === 'number') {
    return model.credits === 0 ? '免费' : `${model.credits} 积分/次`;
  }
  // Record 类型（图片模型分辨率价格）
  const values = Object.values(model.credits);
  const min = Math.min(...values);
  return `${min} 积分起`;
}

interface ModelCardProps {
  model: UnifiedModel;
  isAuthenticated: boolean;
  isSubscribed: boolean;
  isSubscribing: boolean;
  onCardClick: (model: UnifiedModel) => void;
  onSubscribe: (modelId: string) => void;
}

export default function ModelCard({
  model,
  isAuthenticated,
  isSubscribed,
  isSubscribing,
  onCardClick,
  onSubscribe,
}: ModelCardProps) {
  const isFree = typeof model.credits === 'number' && model.credits === 0;
  const capabilities = CAPABILITY_TAGS.filter((tag) => tag.check(model));

  const handleSubscribeClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSubscribe(model.id);
  };

  return (
    <div
      onClick={() => onCardClick(model)}
      className={`bg-white rounded-xl border cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all duration-200 flex flex-col ${
        isSubscribed
          ? 'border-blue-200 bg-blue-50/30'
          : 'border-gray-200'
      }`}
    >
      {/* 内容区 */}
      <div className="p-4 flex-1">
        {/* 标签 */}
        {isFree && (
          <span className="inline-block bg-green-100 text-green-700 text-xs px-2 py-0.5 rounded-full font-medium">
            免费
          </span>
        )}

        {/* 名称 */}
        <h3 className="text-base font-semibold text-gray-900 mt-2 truncate">
          {model.name}
        </h3>

        {/* 描述 */}
        <p className="text-sm text-gray-500 mt-1 line-clamp-1">
          {model.description}
        </p>

        {/* 能力标签 */}
        <div className="flex flex-wrap gap-1.5 mt-3">
          {capabilities.slice(0, 4).map((tag) => (
            <span
              key={tag.key}
              className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600"
            >
              {tag.label}
            </span>
          ))}
          {capabilities.length > 4 && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-400">
              +{capabilities.length - 4}
            </span>
          )}
        </div>

        {/* 费用 */}
        <p className={`text-sm mt-2 font-medium ${isFree ? 'text-green-600' : 'text-gray-600'}`}>
          {getCreditsText(model)}
        </p>
      </div>

      {/* 按钮区（仅已登录显示） */}
      {isAuthenticated &&
        (isSubscribing || isSubscribed ? (
          // 订阅中/已订阅：div 让点击冒泡到外层 → 打开详情抽屉
          <div className="border-t border-gray-100 px-4 py-3 text-center text-sm text-gray-400">
            {isSubscribing ? '订阅中...' : '✓ 已订阅'}
          </div>
        ) : (
          // 未订阅：整条底栏作为按钮，避免 padding 区域误触发开抽屉
          <button
            type="button"
            onClick={handleSubscribeClick}
            className="border-t border-gray-100 px-4 py-3 text-center text-sm font-medium w-full bg-transparent text-blue-600 hover:text-blue-700 hover:bg-blue-50/50 transition-colors"
          >
            订阅
          </button>
        ))}
    </div>
  );
}

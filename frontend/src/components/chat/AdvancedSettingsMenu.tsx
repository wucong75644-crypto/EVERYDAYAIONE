/**
 * 高级设置下拉菜单组件
 *
 * 为图像、视频和聊天模型提供高级参数配置
 */

import { type UnifiedModel } from '../../constants/models';
import {
  ASPECT_RATIOS,
  RESOLUTIONS,
  OUTPUT_FORMATS,
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
} from '../../services/image';
import {
  VIDEO_DURATIONS,
  VIDEO_ASPECT_RATIOS,
  type VideoFrames,
  type VideoAspectRatio,
} from '../../services/video';

interface AdvancedSettingsMenuProps {
  closing?: boolean;
  selectedModel: UnifiedModel;
  // 图像设置
  aspectRatio: AspectRatio;
  onAspectRatioChange: (ratio: AspectRatio) => void;
  resolution: ImageResolution;
  onResolutionChange: (res: ImageResolution) => void;
  outputFormat: ImageOutputFormat;
  onOutputFormatChange: (format: ImageOutputFormat) => void;
  // 视频设置
  videoFrames: VideoFrames;
  onVideoFramesChange: (frames: VideoFrames) => void;
  videoAspectRatio: VideoAspectRatio;
  onVideoAspectRatioChange: (ratio: VideoAspectRatio) => void;
  removeWatermark: boolean;
  onRemoveWatermarkChange: (remove: boolean) => void;
  // 聊天模型设置
  thinkingEffort?: 'minimal' | 'low' | 'medium' | 'high';
  onThinkingEffortChange?: (effort: 'minimal' | 'low' | 'medium' | 'high') => void;
  // 操作
  onSave: () => void;
  onReset: () => void;
  onClose: () => void;
}

// 辅助函数：根据时长计算视频价格
const getVideoPrice = (frames: VideoFrames): number => {
  const duration = VIDEO_DURATIONS.find((d) => d.value === frames);
  return duration?.credits || 90;
};

export default function AdvancedSettingsMenu({
  closing = false,
  selectedModel,
  aspectRatio,
  onAspectRatioChange,
  resolution,
  onResolutionChange,
  outputFormat,
  onOutputFormatChange,
  videoFrames,
  onVideoFramesChange,
  videoAspectRatio,
  onVideoAspectRatioChange,
  removeWatermark,
  onRemoveWatermarkChange,
  thinkingEffort,
  onThinkingEffortChange,
  onSave,
  onReset,
  onClose,
}: AdvancedSettingsMenuProps) {
  return (
    <div
      className={`absolute bottom-full left-0 mb-2 w-80 bg-white rounded-lg shadow-lg border border-gray-200 p-3 z-10 ${
        closing ? 'animate-popupExit' : 'animate-popupEnter'
      }`}
    >
      {/* 图像模型设置 */}
      {selectedModel.type === 'image' && (
        <>
          <div className="mb-3">
            <label className="block text-xs font-medium text-gray-700 mb-2">宽高比</label>
            <div className="flex flex-wrap gap-2">
              {ASPECT_RATIOS.map((ratio) => (
                <button
                  key={ratio.value}
                  onClick={() => onAspectRatioChange(ratio.value)}
                  className={`px-3 py-1 text-xs rounded-md transition-colors ${
                    aspectRatio === ratio.value
                      ? 'bg-purple-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  {ratio.label}
                </button>
              ))}
            </div>
          </div>
          {selectedModel.supportsResolution && (
            <div className="mb-3">
              <label className="block text-xs font-medium text-gray-700 mb-2">分辨率</label>
              <div className="flex gap-2">
                {RESOLUTIONS.map((res) => (
                  <button
                    key={res.value}
                    onClick={() => onResolutionChange(res.value)}
                    className={`flex flex-col items-center px-3 py-1.5 text-xs rounded-md transition-colors ${
                      resolution === res.value
                        ? 'bg-purple-600 text-white'
                        : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                    }`}
                  >
                    <span className="font-medium">{res.label}</span>
                    <span className="text-[10px] opacity-75">{res.credits}积分</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="mb-3">
            <label className="block text-xs font-medium text-gray-700 mb-2">输出格式</label>
            <div className="flex gap-2">
              {OUTPUT_FORMATS.map((format) => (
                <button
                  key={format.value}
                  onClick={() => onOutputFormatChange(format.value)}
                  className={`px-3 py-1 text-xs rounded-md transition-colors ${
                    outputFormat === format.value
                      ? 'bg-purple-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  {format.label}
                </button>
              ))}
            </div>
          </div>
          <div className="bg-blue-50 border border-blue-200 rounded-md px-3 py-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-blue-700 font-medium">预计消耗:</span>
              <span className="text-blue-900 font-semibold">
                {selectedModel.supportsResolution
                  ? `${RESOLUTIONS.find((r) => r.value === resolution)?.credits || 18} 积分`
                  : `${selectedModel.credits} 积分`}
              </span>
            </div>
            <div className="text-[10px] text-blue-600 mt-1">
              ≈ ¥
              {selectedModel.supportsResolution
                ? ((RESOLUTIONS.find((r) => r.value === resolution)?.credits || 18) * 0.036).toFixed(3)
                : (Number(selectedModel.credits) * 0.036).toFixed(3)}
            </div>
          </div>
        </>
      )}

      {/* 视频模型设置 */}
      {selectedModel.type === 'video' && (
        <>
          <div className="mb-3">
            <label className="block text-xs font-medium text-gray-700 mb-2">视频时长</label>
            <div className="flex flex-wrap gap-2">
              {VIDEO_DURATIONS.map((duration) => {
                const price = getVideoPrice(duration.value);
                return (
                  <button
                    key={duration.value}
                    onClick={() => onVideoFramesChange(duration.value)}
                    className={`flex flex-col items-center px-3 py-1.5 text-xs rounded-md transition-colors ${
                      videoFrames === duration.value
                        ? 'bg-purple-600 text-white'
                        : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                    }`}
                  >
                    <span className="font-medium">{duration.label}</span>
                    <span className="text-[10px] opacity-75">{price}积分</span>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="mb-3">
            <label className="block text-xs font-medium text-gray-700 mb-2">宽高比</label>
            <div className="flex gap-2">
              {VIDEO_ASPECT_RATIOS.map((ratio) => (
                <button
                  key={ratio.value}
                  onClick={() => onVideoAspectRatioChange(ratio.value)}
                  className={`px-3 py-1 text-xs rounded-md transition-colors ${
                    videoAspectRatio === ratio.value
                      ? 'bg-purple-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  {ratio.label}
                </button>
              ))}
            </div>
          </div>
          <div className="mb-3">
            <label className="flex items-center space-x-2 cursor-pointer">
              <input
                type="checkbox"
                checked={removeWatermark}
                onChange={(e) => onRemoveWatermarkChange(e.target.checked)}
                className="w-4 h-4 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
              />
              <span className="text-xs text-gray-700">去除水印</span>
            </label>
          </div>
          <div className="bg-blue-50 border border-blue-200 rounded-md px-3 py-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-blue-700 font-medium">预计消耗:</span>
              <span className="text-blue-900 font-semibold">{getVideoPrice(videoFrames)} 积分</span>
            </div>
            <div className="text-[10px] text-blue-600 mt-1">
              ≈ ¥{(getVideoPrice(videoFrames) * 0.036).toFixed(2)}
            </div>
          </div>
        </>
      )}

      {/* 聊天模型设置 */}
      {selectedModel.type === 'chat' && selectedModel.capabilities.thinkingEffort && (
        <>
          <div className="mb-3">
            <label className="block text-xs font-medium text-gray-700 mb-2">
              推理强度 (Thinking Effort)
            </label>
            <div className="space-y-2">
              {[
                { value: 'minimal' as const, label: '极快响应', desc: '最快速度，适合简单问答' },
                { value: 'low' as const, label: '标准速度', desc: '默认选项，平衡速度与质量' },
                { value: 'medium' as const, label: '深度思考', desc: '适合多步推理任务' },
                { value: 'high' as const, label: '最强推理', desc: '接近 Pro 级表现，耗时更长' },
              ].map((effort) => (
                <button
                  key={effort.value}
                  onClick={() => onThinkingEffortChange?.(effort.value)}
                  className={`w-full px-3 py-2 text-left rounded-md transition-colors ${
                    thinkingEffort === effort.value
                      ? 'bg-blue-100 border border-blue-400'
                      : 'bg-gray-50 border border-gray-200 hover:bg-gray-100'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1">
                      <div className="text-xs font-medium text-gray-900">{effort.label}</div>
                      <div className="text-[10px] text-gray-600 mt-0.5">{effort.desc}</div>
                    </div>
                    {thinkingEffort === effort.value && (
                      <svg className="w-4 h-4 text-blue-600 flex-shrink-0 ml-2" fill="currentColor" viewBox="0 0 20 20">
                        <path
                          fillRule="evenodd"
                          d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                          clipRule="evenodd"
                        />
                      </svg>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
          <div className="bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
            <div className="flex items-start space-x-2">
              <svg className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                <path
                  fillRule="evenodd"
                  d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                  clipRule="evenodd"
                />
              </svg>
              <div className="flex-1 text-[10px] text-amber-800">
                <div className="font-medium mb-1">推理强度越高：</div>
                <ul className="list-disc list-inside space-y-0.5 text-[9px]">
                  <li>推理深度更强，适合复杂任务</li>
                  <li>响应时间更长</li>
                  <li>更接近 Gemini 3 Pro 的表现</li>
                </ul>
              </div>
            </div>
          </div>
        </>
      )}

      {/* 底部操作按钮 */}
      <div className="mt-3 pt-3 border-t border-gray-200 flex gap-2">
        <button
          onClick={() => {
            onSave();
            onClose();
          }}
          className="flex-1 px-3 py-1.5 text-xs bg-purple-600 text-white rounded-md hover:bg-purple-700 transition-colors"
        >
          保存为默认
        </button>
        <button
          onClick={onReset}
          className="flex-1 px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 transition-colors"
        >
          恢复默认
        </button>
      </div>
    </div>
  );
}

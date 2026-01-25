/**
 * 音频预览组件
 *
 * 显示录音完成后的音频播放器，提供删除功能
 */

interface AudioPreviewProps {
  audioURL: string;
  recordingTime: number;
  onClear: () => void;
}

export default function AudioPreview({ audioURL, recordingTime, onClear }: AudioPreviewProps) {
  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  return (
    <div className="mb-2 pb-2 border-b border-gray-100">
      <div className="flex items-center space-x-3 px-3 py-2 bg-blue-50 rounded-lg">
        {/* 音频图标 */}
        <svg className="w-5 h-5 text-blue-600 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"
          />
        </svg>

        {/* 音频播放器 */}
        <div className="flex-1 min-w-0">
          <audio src={audioURL} controls className="w-full h-8" />
        </div>

        {/* 时长显示 */}
        <span className="text-xs text-gray-600 flex-shrink-0">
          {formatTime(recordingTime)}
        </span>

        {/* 删除按钮 */}
        <button
          onClick={onClear}
          className="flex-shrink-0 w-6 h-6 bg-gray-800 text-white rounded-full flex items-center justify-center hover:bg-gray-700 transition-colors"
          title="删除录音"
        >
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}

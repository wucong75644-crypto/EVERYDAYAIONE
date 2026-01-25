/**
 * 音频录制组件
 */

import { Mic, Square } from 'lucide-react';

interface AudioRecorderProps {
  isRecording: boolean;
  recordingTime: number;
  audioURL: string | null;
  onStartRecording: () => void;
  onStopRecording: () => void;
  onClearAudio: () => void;
  disabled?: boolean;
}

export default function AudioRecorder({
  isRecording,
  recordingTime,
  audioURL,
  onStartRecording,
  onStopRecording,
  onClearAudio,
  disabled = false,
}: AudioRecorderProps) {
  return (
    <>
      {/* 录音按钮 */}
      <button
        onClick={isRecording ? onStopRecording : onStartRecording}
        disabled={disabled}
        className={`p-2 rounded-lg transition-colors ${
          isRecording
            ? 'bg-red-500 text-white hover:bg-red-600'
            : 'text-gray-600 hover:bg-gray-100'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
        title={isRecording ? '停止录音' : '开始录音'}
      >
        {isRecording ? <Square className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
      </button>

      {/* 录音时长显示 */}
      {isRecording && (
        <span className="text-sm text-red-500 font-medium">
          {Math.floor(recordingTime / 60)}:{(recordingTime % 60).toString().padStart(2, '0')}
        </span>
      )}

      {/* 音频预览 */}
      {audioURL && !isRecording && (
        <div className="flex items-center space-x-2 px-3 py-1 bg-blue-50 rounded-lg">
          <audio src={audioURL} controls className="h-8" />
          <button
            onClick={onClearAudio}
            className="text-red-500 hover:text-red-700 transition-colors"
            title="删除录音"
          >
            ✕
          </button>
        </div>
      )}
    </>
  );
}

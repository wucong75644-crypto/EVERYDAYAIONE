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
  audioURL: _audioURL,
  onStartRecording,
  onStopRecording,
  onClearAudio: _onClearAudio,
  disabled = false,
}: AudioRecorderProps) {
  // 预留参数供后续音频预览功能使用
  void _audioURL;
  void _onClearAudio;
  return (
    <>
      {/* 录音按钮 */}
      <button
        onClick={isRecording ? onStopRecording : onStartRecording}
        disabled={disabled}
        className={`p-2 rounded-lg transition-base ${
          isRecording
            ? 'bg-error text-white hover:bg-error/90'
            : 'text-text-tertiary hover:bg-hover'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
        title={isRecording ? '停止录音' : '开始录音'}
      >
        {isRecording ? <Square className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
      </button>

      {/* 录音时长显示 */}
      {isRecording && (
        <span className="text-sm text-error font-medium">
          {Math.floor(recordingTime / 60)}:{(recordingTime % 60).toString().padStart(2, '0')}
        </span>
      )}
    </>
  );
}

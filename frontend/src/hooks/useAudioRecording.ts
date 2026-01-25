/**
 * 音频录制 Hook
 *
 * 使用 Web Audio API 实现音频录制功能
 */

import { useState, useRef, useCallback } from 'react';

export type RecordingState = 'idle' | 'recording' | 'paused' | 'stopped';

interface UseAudioRecordingReturn {
  recordingState: RecordingState;
  audioBlob: Blob | null;
  audioDuration: number;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  pauseRecording: () => void;
  resumeRecording: () => void;
  clearRecording: () => void;
  error: string | null;
}

export function useAudioRecording(): UseAudioRecordingReturn {
  const [recordingState, setRecordingState] = useState<RecordingState>('idle');
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioDuration, setAudioDuration] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const startTimeRef = useRef<number>(0);
  const durationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /**
   * 开始录音
   */
  const startRecording = useCallback(async () => {
    try {
      setError(null);

      // 请求麦克风权限
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 44100,
        },
      });

      // 创建 MediaRecorder
      const mimeType = MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : 'audio/mp4';

      const mediaRecorder = new MediaRecorder(stream, {
        mimeType,
      });

      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      // 监听数据可用事件
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      // 监听停止事件
      mediaRecorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: mimeType });
        setAudioBlob(blob);
        setRecordingState('stopped');

        // 停止所有音轨
        stream.getTracks().forEach((track) => track.stop());

        // 清理定时器
        if (durationTimerRef.current) {
          clearInterval(durationTimerRef.current);
          durationTimerRef.current = null;
        }
      };

      // 开始录音
      mediaRecorder.start();
      setRecordingState('recording');
      startTimeRef.current = Date.now();

      // 启动时长计时器
      durationTimerRef.current = setInterval(() => {
        const elapsed = (Date.now() - startTimeRef.current) / 1000;
        setAudioDuration(elapsed);
      }, 100);
    } catch (err) {
      console.error('录音失败:', err);
      setError(
        err instanceof Error
          ? err.message
          : '无法访问麦克风，请检查权限设置'
      );
      setRecordingState('idle');
    }
  }, []);

  /**
   * 停止录音
   */
  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && recordingState !== 'idle') {
      mediaRecorderRef.current.stop();
    }
  }, [recordingState]);

  /**
   * 暂停录音
   */
  const pauseRecording = useCallback(() => {
    if (mediaRecorderRef.current && recordingState === 'recording') {
      mediaRecorderRef.current.pause();
      setRecordingState('paused');

      // 暂停计时器
      if (durationTimerRef.current) {
        clearInterval(durationTimerRef.current);
        durationTimerRef.current = null;
      }
    }
  }, [recordingState]);

  /**
   * 恢复录音
   */
  const resumeRecording = useCallback(() => {
    if (mediaRecorderRef.current && recordingState === 'paused') {
      mediaRecorderRef.current.resume();
      setRecordingState('recording');

      // 恢复计时器
      const pausedTime = audioDuration * 1000;
      startTimeRef.current = Date.now() - pausedTime;

      durationTimerRef.current = setInterval(() => {
        const elapsed = (Date.now() - startTimeRef.current) / 1000;
        setAudioDuration(elapsed);
      }, 100);
    }
  }, [recordingState, audioDuration]);

  /**
   * 清除录音
   */
  const clearRecording = useCallback(() => {
    setAudioBlob(null);
    setAudioDuration(0);
    setRecordingState('idle');
    setError(null);
    audioChunksRef.current = [];
  }, []);

  return {
    recordingState,
    audioBlob,
    audioDuration,
    startRecording,
    stopRecording,
    pauseRecording,
    resumeRecording,
    clearRecording,
    error,
  };
}

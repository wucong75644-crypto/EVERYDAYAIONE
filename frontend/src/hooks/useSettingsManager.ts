/**
 * 设置管理 Hook
 *
 * 管理图像/视频/聊天参数状态，支持持久化存储
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import {
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
  type ImageCount,
  type VideoFrames,
  type VideoAspectRatio,
} from '../constants/models';
import {
  getSavedSettings,
  saveSettings as persistSettings,
  resetSettings as clearSettings,
  type UserAdvancedSettings,
} from '../utils/settingsStorage';
import type { ChatSettings as ConversationChatSettings } from '../services/conversation';
import { useAuthStore } from '../stores/useAuthStore';
import { clearNewConversationSettings, getPendingConversationSettings, retryPendingConversationSettings, saveConversationSettings } from '../utils/conversationSettingsPersistence';

// ============================================================
// 类型定义
// ============================================================

export interface ImageSettings {
  aspectRatio: AspectRatio;
  resolution: ImageResolution;
  outputFormat: ImageOutputFormat;
  numImages: ImageCount;
  taobaoMainImage: boolean;
}

export interface VideoSettings {
  frames: VideoFrames;
  aspectRatio: VideoAspectRatio;
  removeWatermark: boolean;
}

export type PermissionMode = 'auto' | 'ask' | 'plan';

export type SmartSubMode = 'chat' | 'image-i2i' | 'image-t2i' | 'image-ecom' | 'video';

export interface ChatSettings {
  smartSubMode: SmartSubMode;
  thinkingEffort: 'minimal' | 'low' | 'medium' | 'high';
  deepThinkMode: boolean;
  permissionMode: PermissionMode;  // 权限模式：auto/ask/plan
  temperature: number;      // 0.0 - 2.0
  topP: number;            // 0.0 - 1.0
  topK: number;            // 1 - 64
  maxOutputTokens: number; // 1 - 65536
}

export interface UseSettingsManagerReturn {
  // 图像设置
  imageSettings: ImageSettings;
  setImageSetting: <K extends keyof ImageSettings>(key: K, value: ImageSettings[K]) => void;

  // 视频设置
  videoSettings: VideoSettings;
  setVideoSetting: <K extends keyof VideoSettings>(key: K, value: VideoSettings[K]) => void;

  // 聊天设置
  chatSettings: ChatSettings;
  setChatSetting: <K extends keyof ChatSettings>(key: K, value: ChatSettings[K]) => void;

  // 持久化操作
  saveSettings: () => void;
  resetSettings: () => void;
}

// ============================================================
// Hook 实现
// ============================================================

/** 系统默认值（新建对话时使用） */
const DEFAULTS = {
  image: { aspectRatio: '1:1' as AspectRatio, resolution: '1024x1024' as ImageResolution, outputFormat: 'png' as ImageOutputFormat, numImages: 1 as ImageCount, taobaoMainImage: false },
  video: { frames: '10' as VideoFrames, aspectRatio: 'landscape' as VideoAspectRatio, removeWatermark: false },
  chat: { smartSubMode: 'chat' as SmartSubMode, thinkingEffort: 'low' as const, deepThinkMode: false, permissionMode: 'auto' as PermissionMode, temperature: 1.0, topP: 0.95, topK: 40, maxOutputTokens: 8192 },
};

export function useSettingsManager(
  conversationId?: string | null,
  conversationChatSettings?: ConversationChatSettings | null,
): UseSettingsManagerReturn {
  // 加载保存的设置（localStorage 作为全局默认的兜底）
  const savedSettings = getSavedSettings();
  // 对话级设置（优先）> localStorage（兜底）> 系统默认值
  const cs = conversationChatSettings;
  const userId = useAuthStore((state) => state.user?.id) ?? 'anonymous';

  // 图像生成参数
  const [imageSettings, setImageSettings] = useState<ImageSettings>({
    aspectRatio: (cs?.image_aspect_ratio as AspectRatio) || savedSettings.image.aspectRatio,
    resolution: (cs?.image_resolution as ImageResolution) || savedSettings.image.resolution,
    outputFormat: (cs?.image_output_format as ImageOutputFormat) || savedSettings.image.outputFormat,
    numImages: (cs?.image_num_images as ImageCount) ?? savedSettings.image.numImages,
    taobaoMainImage: ((cs?.image_aspect_ratio || savedSettings.image.aspectRatio) === '1:1') &&
      (cs?.image_taobao_main_image ?? (conversationId ? false : savedSettings.image.taobaoMainImage ?? false)),
  });

  // 视频生成参数
  const [videoSettings, setVideoSettings] = useState<VideoSettings>({
    frames: (cs?.video_frames as VideoFrames) ?? savedSettings.video.frames,
    aspectRatio: (cs?.video_aspect_ratio as VideoAspectRatio) || savedSettings.video.aspectRatio,
    removeWatermark: cs?.video_remove_watermark ?? savedSettings.video.removeWatermark,
  });

  // 聊天模型参数
  const [chatSettings, setChatSettings] = useState<ChatSettings>({
    smartSubMode: (cs?.smart_sub_mode as SmartSubMode) || DEFAULTS.chat.smartSubMode,
    thinkingEffort: (cs?.thinking_effort as ChatSettings['thinkingEffort']) || savedSettings.chat?.thinkingEffort || DEFAULTS.chat.thinkingEffort,
    deepThinkMode: cs?.deep_think_mode ?? DEFAULTS.chat.deepThinkMode,
    permissionMode: (savedSettings.chat as Partial<ChatSettings>)?.permissionMode || DEFAULTS.chat.permissionMode,
    temperature: cs?.temperature ?? savedSettings.chat?.temperature ?? DEFAULTS.chat.temperature,
    topP: cs?.top_p ?? savedSettings.chat?.topP ?? DEFAULTS.chat.topP,
    topK: cs?.top_k ?? savedSettings.chat?.topK ?? DEFAULTS.chat.topK,
    maxOutputTokens: cs?.max_output_tokens ?? savedSettings.chat?.maxOutputTokens ?? DEFAULTS.chat.maxOutputTokens,
  });

  const settingsRef = useRef({ image: imageSettings, video: videoSettings, chat: chatSettings });
  const editedConversationRef = useRef<string | null>(null);
  const restoredConversationRef = useRef<string | null>(null);

  // API responses received after an edit must not restore an older selection.
  useEffect(() => {
    const identity = `${userId}:${conversationId ?? 'new'}`;
    if (restoredConversationRef.current === `${userId}:new` && conversationId) {
      clearNewConversationSettings(userId);
    }
    restoredConversationRef.current = identity;
    if (editedConversationRef.current === identity) return;
    const pending = getPendingConversationSettings(userId, conversationId ?? 'new');
    const source = pending?.settings ?? conversationChatSettings;
    const imageDefaults = conversationId ? DEFAULTS.image : getSavedSettings().image;
    const aspectRatio = (source?.image_aspect_ratio as AspectRatio) || imageDefaults.aspectRatio;
    const image: ImageSettings = {
      aspectRatio,
      resolution: (source?.image_resolution as ImageResolution) || imageDefaults.resolution,
      outputFormat: (source?.image_output_format as ImageOutputFormat) || imageDefaults.outputFormat,
      numImages: (source?.image_num_images as ImageCount) ?? imageDefaults.numImages,
      taobaoMainImage: aspectRatio === '1:1' &&
        (source?.image_taobao_main_image ?? (conversationId ? false : imageDefaults.taobaoMainImage ?? false)),
    };
    const video: VideoSettings = {
      frames: (source?.video_frames as VideoFrames) ?? DEFAULTS.video.frames,
      aspectRatio: (source?.video_aspect_ratio as VideoAspectRatio) || DEFAULTS.video.aspectRatio,
      removeWatermark: source?.video_remove_watermark ?? DEFAULTS.video.removeWatermark,
    };
    const chat: ChatSettings = {
      smartSubMode: (source?.smart_sub_mode as SmartSubMode) || DEFAULTS.chat.smartSubMode,
      thinkingEffort: (source?.thinking_effort as ChatSettings['thinkingEffort']) || DEFAULTS.chat.thinkingEffort,
      deepThinkMode: source?.deep_think_mode ?? DEFAULTS.chat.deepThinkMode,
      permissionMode: DEFAULTS.chat.permissionMode,
      temperature: source?.temperature ?? DEFAULTS.chat.temperature,
      topP: source?.top_p ?? DEFAULTS.chat.topP,
      topK: source?.top_k ?? DEFAULTS.chat.topK,
      maxOutputTokens: source?.max_output_tokens ?? DEFAULTS.chat.maxOutputTokens,
    };
    settingsRef.current = { image, video, chat };
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Restore persisted settings when the conversation changes.
    setImageSettings(image);
    setVideoSettings(video);
    setChatSettings(chat);
    editedConversationRef.current = pending ? identity : null;
    if (pending && conversationId) retryPendingConversationSettings(userId, conversationId);
  }, [conversationId, conversationChatSettings, userId]);

  const autoSaveToConversation = useCallback((
    img: ImageSettings, vid: VideoSettings, chat: ChatSettings,
  ) => {
    editedConversationRef.current = `${userId}:${conversationId ?? 'new'}`;
    const payload: ConversationChatSettings = {
      smart_sub_mode: chat.smartSubMode,
      deep_think_mode: chat.deepThinkMode,
      thinking_effort: chat.thinkingEffort,
      temperature: chat.temperature,
      top_p: chat.topP,
      top_k: chat.topK,
      max_output_tokens: chat.maxOutputTokens,
      image_aspect_ratio: img.aspectRatio,
      image_resolution: img.resolution,
      image_output_format: img.outputFormat,
      image_num_images: img.numImages,
      image_taobao_main_image: img.taobaoMainImage,
      video_frames: vid.frames,
      video_aspect_ratio: vid.aspectRatio,
      video_remove_watermark: vid.removeWatermark,
    };
    saveConversationSettings(userId, conversationId ?? null, payload);
  }, [conversationId, userId]);

  const setImageSetting = useCallback(
    <K extends keyof ImageSettings>(key: K, value: ImageSettings[K]) => {
      const next = { ...settingsRef.current.image, [key]: value };
      if (next.aspectRatio !== '1:1') next.taobaoMainImage = false;
      settingsRef.current.image = next;
      setImageSettings(next);
      autoSaveToConversation(next, settingsRef.current.video, settingsRef.current.chat);
    }, [autoSaveToConversation],
  );

  const setVideoSetting = useCallback(
    <K extends keyof VideoSettings>(key: K, value: VideoSettings[K]) => {
      const next = { ...settingsRef.current.video, [key]: value };
      settingsRef.current.video = next;
      setVideoSettings(next);
      autoSaveToConversation(settingsRef.current.image, next, settingsRef.current.chat);
    }, [autoSaveToConversation],
  );

  const setChatSetting = useCallback(
    <K extends keyof ChatSettings>(key: K, value: ChatSettings[K]) => {
      const next = { ...settingsRef.current.chat, [key]: value };
      settingsRef.current.chat = next;
      setChatSettings(next);
      autoSaveToConversation(settingsRef.current.image, settingsRef.current.video, next);
    }, [autoSaveToConversation],
  );

  // 保存当前设置为默认值
  const saveSettings = useCallback(() => {
    const settings: UserAdvancedSettings = {
      image: {
        aspectRatio: imageSettings.aspectRatio,
        resolution: imageSettings.resolution,
        outputFormat: imageSettings.outputFormat,
        numImages: imageSettings.numImages,
        taobaoMainImage: imageSettings.taobaoMainImage,
      },
      video: {
        frames: videoSettings.frames,
        aspectRatio: videoSettings.aspectRatio,
        removeWatermark: videoSettings.removeWatermark,
      },
      chat: {
        thinkingEffort: chatSettings.thinkingEffort,
        temperature: chatSettings.temperature,
        topP: chatSettings.topP,
        topK: chatSettings.topK,
        maxOutputTokens: chatSettings.maxOutputTokens,
      },
    };
    persistSettings(settings);
  }, [imageSettings, videoSettings, chatSettings]);

  // 重置为默认设置
  const resetSettings = useCallback(() => {
    const defaults = clearSettings();
    const image: ImageSettings = { ...defaults.image, taobaoMainImage: false };
    const video: VideoSettings = { ...defaults.video };
    const chat: ChatSettings = {
      ...DEFAULTS.chat,
      thinkingEffort: defaults.chat.thinkingEffort,
      temperature: defaults.chat.temperature,
      topP: defaults.chat.topP,
      topK: defaults.chat.topK,
      maxOutputTokens: defaults.chat.maxOutputTokens,
    };
    settingsRef.current = { image, video, chat };
    setImageSettings(image);
    setVideoSettings(video);
    setChatSettings(chat);
    autoSaveToConversation(image, video, chat);
  }, [autoSaveToConversation]);

  return {
    imageSettings,
    setImageSetting,
    videoSettings,
    setVideoSetting,
    chatSettings,
    setChatSetting,
    saveSettings,
    resetSettings,
  };
}

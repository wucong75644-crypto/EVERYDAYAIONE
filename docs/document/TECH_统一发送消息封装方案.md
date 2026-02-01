# 统一发送消息封装方案

> **文档版本**：v1.0
> **创建日期**：2026-02-01
> **状态**：待实施
> **优先级**：高

---

## 一、方案目标

### 核心目标

1. **统一发送逻辑**：聊天、图片、视频等所有类型使用统一入口
2. **复用最大化**：首次发送 + 成功重新生成复用同一套逻辑
3. **持久化友好**：后续持久化只需修改一处，调用方无感知
4. **可扩展性**：为后续新增媒体类型（音频、3D 等）预留扩展点

### 设计原则

| 场景 | 行为 | 调用方案 |
|-----|------|---------|
| 首次发送 | 末尾新增消息对 | `sendMessage()` |
| 成功重新生成 | 末尾新增消息对 | `sendMessage()`（复用） |
| 失败重新生成 | 原地替换 | `regenerateInPlace()`（独立） |

---

## 二、目录结构

```
frontend/src/services/messageSender/
├── index.ts           # 统一入口
├── types.ts           # 类型定义
├── chatSender.ts      # 聊天发送逻辑
├── imageSender.ts     # 图片发送逻辑
└── videoSender.ts     # 视频发送逻辑
```

---

## 三、类型定义（types.ts）

```typescript
/**
 * 统一发送消息 - 类型定义
 */

import type { Message, GenerationParams } from '../message';

/** 媒体类型（可扩展） */
export type MediaType = 'chat' | 'image' | 'video';

/** 发送消息回调 */
export interface SendMessageCallbacks {
  /** 消息待处理（乐观更新） */
  onMessagePending: (message: Message) => void;
  /** 消息发送完成（成功或失败） */
  onMessageSent: (aiMessage?: Message | null) => void;
  /** 流式内容更新（仅聊天） */
  onStreamContent?: (text: string, conversationId: string) => void;
  /** 流式开始（仅聊天） */
  onStreamStart?: (conversationId: string, modelId: string) => void;
  /** 媒体任务已提交（图片/视频） */
  onMediaTaskSubmitted?: () => void;
}

/** 发送消息基础参数 */
export interface SendMessageParams {
  /** 消息类型 */
  type: MediaType;
  /** 对话ID */
  conversationId: string;
  /** 消息内容 */
  content: string;
  /** 附带图片URL（可选） */
  imageUrl?: string | null;

  /** 模型ID */
  modelId: string;
  /** 生成参数（图片/视频需要） */
  generationParams?: GenerationParams;

  /** 对话标题（媒体任务需要） */
  conversationTitle?: string;

  /** 客户端请求ID（可选，用于去重） */
  clientRequestId?: string;

  /** 回调函数 */
  callbacks: SendMessageCallbacks;
}

/** 聊天特有参数 */
export interface ChatSenderParams extends SendMessageParams {
  type: 'chat';
  /** 思考力度 */
  thinkingEffort?: 'minimal' | 'low' | 'medium' | 'high';
  /** 深度思考模式 */
  deepThinkMode?: boolean;
  /** 跳过乐观更新（重新生成时用） */
  skipOptimisticUpdate?: boolean;
}

/** 图片特有参数 */
export interface ImageSenderParams extends SendMessageParams {
  type: 'image';
  /** 必须有图片生成参数 */
  generationParams: GenerationParams & { image: NonNullable<GenerationParams['image']> };
}

/** 视频特有参数 */
export interface VideoSenderParams extends SendMessageParams {
  type: 'video';
  /** 必须有视频生成参数 */
  generationParams: GenerationParams & { video: NonNullable<GenerationParams['video']> };
}
```

---

## 四、统一入口（index.ts）

```typescript
/**
 * 统一发送消息入口
 *
 * 使用场景：
 * 1. 首次发送消息
 * 2. 成功消息重新生成
 *
 * 后续持久化只需修改此处和各 sender，调用方无感知
 */

import { sendChatMessage } from './chatSender';
import { sendImageMessage } from './imageSender';
import { sendVideoMessage } from './videoSender';
import type { SendMessageParams, ChatSenderParams, ImageSenderParams, VideoSenderParams } from './types';

export * from './types';

/**
 * 统一发送消息
 * @param params 发送参数
 */
export async function sendMessage(
  params: ChatSenderParams | ImageSenderParams | VideoSenderParams
): Promise<void> {
  const { type } = params;

  switch (type) {
    case 'chat':
      return sendChatMessage(params);
    case 'image':
      return sendImageMessage(params);
    case 'video':
      return sendVideoMessage(params);
    default:
      // 穷尽检查：确保类型全覆盖，后续新增类型未处理时编译报错
      const _exhaustiveCheck: never = type;
      throw new Error(`不支持的消息类型: ${_exhaustiveCheck}`);
  }
}

/**
 * 便捷方法：发送聊天消息
 */
export { sendChatMessage } from './chatSender';

/**
 * 便捷方法：发送图片消息
 */
export { sendImageMessage } from './imageSender';

/**
 * 便捷方法：发送视频消息
 */
export { sendVideoMessage } from './videoSender';
```

---

## 五、聊天发送器（chatSender.ts）

```typescript
/**
 * 聊天消息发送器
 * 从 useTextMessageHandler 提取核心逻辑
 */

import { sendMessageStream } from '../message';
import { createOptimisticUserMessage, createErrorMessage } from '../../utils/messageFactory';
import { useChatStore } from '../../stores/useChatStore';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import type { ChatSenderParams } from './types';

export async function sendChatMessage(params: ChatSenderParams): Promise<void> {
  const {
    conversationId,
    content,
    imageUrl,
    modelId,
    thinkingEffort,
    deepThinkMode,
    clientRequestId,
    skipOptimisticUpdate = false,
    callbacks,
  } = params;

  const { onMessagePending, onMessageSent, onStreamContent, onStreamStart } = callbacks;

  // 1. 乐观更新（可跳过）
  if (!skipOptimisticUpdate) {
    const optimisticUserMessage = createOptimisticUserMessage(
      content,
      conversationId,
      imageUrl ?? null,
      undefined,
      clientRequestId
    );
    onMessagePending(optimisticUserMessage);
  }

  if (onStreamStart) onStreamStart(conversationId, modelId);

  // 2. 发送流式请求
  try {
    await sendMessageStream(
      conversationId,
      {
        content,
        model_id: modelId,
        image_url: imageUrl ?? null,
        thinking_effort: thinkingEffort,
        thinking_mode: deepThinkMode ? 'deep_think' : 'default',
        client_request_id: clientRequestId,
      },
      {
        onUserMessage: (userMessage) => {
          if (userMessage.client_request_id) {
            useChatStore.getState().updateMessageId(
              conversationId,
              userMessage.client_request_id,
              userMessage.id
            );
            useConversationRuntimeStore.getState().updateMessageId(
              conversationId,
              userMessage.client_request_id,
              userMessage.id
            );
          } else {
            onMessagePending(userMessage);
          }
        },
        onStart: () => {},
        onContent: (text) => {
          if (onStreamContent) onStreamContent(text, conversationId);
        },
        onDone: (assistantMessage) => onMessageSent(assistantMessage ?? null),
        onError: (error) => {
          onMessageSent(createErrorMessage(conversationId, 'AI 响应错误', error));
        },
      }
    );
  } catch (error) {
    onMessageSent(createErrorMessage(conversationId, '发送失败', error));
  }
}
```

---

## 六、图片发送器（imageSender.ts）

```typescript
/**
 * 图片消息发送器
 * 从 useImageMessageHandler 提取核心逻辑
 */

import { createMessage } from '../message';
import { generateImage, editImage, queryTaskStatus as getImageTaskStatus } from '../image';
import { createMediaTimestamps, createMediaOptimisticPair } from '../../utils/messageFactory';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { createMediaPollingHandler, handleGenerationError, extractImageUrl } from '../../hooks/handlers/mediaHandlerUtils';
import { IMAGE_TASK_TIMEOUT, IMAGE_POLL_INTERVAL } from '../../config/task';
import type { ImageSenderParams } from './types';
import type { ImageModel } from '../image';

export async function sendImageMessage(params: ImageSenderParams): Promise<void> {
  const {
    conversationId,
    content,
    imageUrl,
    modelId,
    generationParams,
    conversationTitle = '',
    callbacks,
  } = params;

  const { onMessagePending, onMessageSent, onMediaTaskSubmitted } = callbacks;
  const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();

  const imageParams = generationParams.image;
  const { aspectRatio, outputFormat, resolution } = imageParams;

  // 1. 创建时间戳和乐观消息对
  const timestamps = createMediaTimestamps();
  const { tempPlaceholderId, userTimestamp, placeholderTimestamp } = timestamps;

  const { userMessage, placeholder } = createMediaOptimisticPair(
    conversationId,
    content,
    imageUrl ?? null,
    '图片生成中...',
    timestamps
  );
  onMessagePending(userMessage);
  onMessagePending(placeholder);

  try {
    // 2. 并行：保存用户消息 + 调用生成API
    const [, response] = await Promise.all([
      createMessage(conversationId, {
        content,
        role: 'user',
        image_url: imageUrl ?? null,
        created_at: userTimestamp,
      }).then((realUserMessage) => onMessagePending(realUserMessage)),
      imageUrl
        ? editImage({
            prompt: content,
            image_urls: imageUrl.split(',').map(url => url.trim()).filter(Boolean),
            size: aspectRatio,
            output_format: outputFormat,
            wait_for_result: false,
            conversation_id: conversationId,
          })
        : generateImage({
            prompt: content,
            model: modelId as ImageModel,
            size: aspectRatio,
            output_format: outputFormat,
            resolution,
            wait_for_result: false,
            conversation_id: conversationId,
          }),
    ]);

    const successContent = imageUrl ? '图片编辑完成' : '图片已生成完成';

    // 3. 处理响应
    if (response.status === 'pending' || response.status === 'processing') {
      createMediaPollingHandler(response, {
        type: 'image',
        conversationId,
        conversationTitle,
        successContent,
        errorPrefix: '图片处理失败',
        pollInterval: IMAGE_POLL_INTERVAL,
        maxDuration: IMAGE_TASK_TIMEOUT,
        creditsConsumed: response.credits_consumed,
        userMessageTimestamp: userTimestamp,
        placeholderTimestamp,
        preCreatedPlaceholderId: tempPlaceholderId,
        generationParams,
        pollFn: getImageTaskStatus,
        extractMediaUrl: (r) => ({ image_url: extractImageUrl(r) }),
        shouldPreloadImage: true,
      }, { onMessagePending, onMessageSent, onMediaTaskSubmitted });
    } else if (response.status === 'success' && response.image_urls?.length) {
      const savedAiMessage = await createMessage(conversationId, {
        content: successContent,
        role: 'assistant',
        image_url: response.image_urls[0],
        credits_cost: response.credits_consumed,
        created_at: placeholderTimestamp,
        generation_params: generationParams,
      });
      replaceMediaPlaceholder(conversationId, tempPlaceholderId, savedAiMessage);
      onMessageSent(savedAiMessage);
      onMediaTaskSubmitted?.();
    } else {
      throw new Error('图片处理失败');
    }
  } catch (error) {
    const errorMessage = await handleGenerationError(
      conversationId,
      '图片处理失败',
      error,
      placeholderTimestamp,
      generationParams
    );
    replaceMediaPlaceholder(conversationId, tempPlaceholderId, errorMessage);
    onMessageSent(errorMessage);
    onMediaTaskSubmitted?.();
  }
}
```

---

## 七、视频发送器（videoSender.ts）

```typescript
/**
 * 视频消息发送器
 * 从 useVideoMessageHandler 提取核心逻辑
 */

import { createMessage } from '../message';
import { generateTextToVideo, generateImageToVideo, queryVideoTaskStatus as getVideoTaskStatus } from '../video';
import { createMediaTimestamps, createMediaOptimisticPair } from '../../utils/messageFactory';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { createMediaPollingHandler, handleGenerationError, extractVideoUrl } from '../../hooks/handlers/mediaHandlerUtils';
import { VIDEO_TASK_TIMEOUT, VIDEO_POLL_INTERVAL } from '../../config/task';
import { ALL_MODELS } from '../../constants/models';
import type { VideoSenderParams } from './types';
import type { VideoModel } from '../video';

export async function sendVideoMessage(params: VideoSenderParams): Promise<void> {
  const {
    conversationId,
    content,
    imageUrl,
    modelId,
    generationParams,
    conversationTitle = '',
    callbacks,
  } = params;

  const { onMessagePending, onMessageSent, onMediaTaskSubmitted } = callbacks;
  const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();

  const videoParams = generationParams.video;
  const { frames, aspectRatio, removeWatermark } = videoParams;

  // 判断是否图生视频
  const modelConfig = ALL_MODELS.find(m => m.id === modelId);
  const supportsI2V = modelConfig?.type === 'video' && modelConfig.capabilities.imageToVideo;
  const isImageToVideo = imageUrl && supportsI2V;

  // 1. 创建时间戳和乐观消息对
  const timestamps = createMediaTimestamps();
  const { tempPlaceholderId, userTimestamp, placeholderTimestamp } = timestamps;

  const { userMessage, placeholder } = createMediaOptimisticPair(
    conversationId,
    content,
    isImageToVideo ? imageUrl : null,
    '视频生成中...',
    timestamps
  );
  onMessagePending(userMessage);
  onMessagePending(placeholder);

  try {
    // 2. 并行：保存用户消息 + 调用生成API
    const [, response] = await Promise.all([
      createMessage(conversationId, {
        content,
        role: 'user',
        image_url: isImageToVideo ? imageUrl : null,
        created_at: userTimestamp,
      }).then((realUserMessage) => onMessagePending(realUserMessage)),
      isImageToVideo
        ? generateImageToVideo({
            prompt: content,
            image_url: imageUrl,
            model: modelId as VideoModel,
            n_frames: frames,
            aspect_ratio: aspectRatio,
            remove_watermark: removeWatermark,
            wait_for_result: false,
            conversation_id: conversationId,
          })
        : generateTextToVideo({
            prompt: content,
            model: modelId as VideoModel,
            n_frames: frames,
            aspect_ratio: aspectRatio,
            remove_watermark: removeWatermark,
            wait_for_result: false,
            conversation_id: conversationId,
          }),
    ]);

    const successContent = isImageToVideo ? '视频生成完成（图生视频）' : '视频生成完成';

    // 3. 处理响应
    if (response.status === 'pending' || response.status === 'processing') {
      createMediaPollingHandler(response, {
        type: 'video',
        conversationId,
        conversationTitle,
        successContent,
        errorPrefix: '视频生成失败',
        pollInterval: VIDEO_POLL_INTERVAL,
        maxDuration: VIDEO_TASK_TIMEOUT,
        creditsConsumed: response.credits_consumed,
        userMessageTimestamp: userTimestamp,
        placeholderTimestamp,
        preCreatedPlaceholderId: tempPlaceholderId,
        generationParams,
        pollFn: getVideoTaskStatus,
        extractMediaUrl: (r) => ({ video_url: extractVideoUrl(r) }),
        shouldPreloadImage: false,
      }, { onMessagePending, onMessageSent, onMediaTaskSubmitted });
    } else if (response.status === 'success' && response.video_url) {
      const savedAiMessage = await createMessage(conversationId, {
        content: successContent,
        role: 'assistant',
        video_url: response.video_url,
        credits_cost: response.credits_consumed,
        created_at: placeholderTimestamp,
        generation_params: generationParams,
      });
      replaceMediaPlaceholder(conversationId, tempPlaceholderId, savedAiMessage);
      onMessageSent(savedAiMessage);
      onMediaTaskSubmitted?.();
    } else {
      throw new Error('视频生成失败');
    }
  } catch (error) {
    const errorMessage = await handleGenerationError(
      conversationId,
      '视频生成失败',
      error,
      placeholderTimestamp,
      generationParams
    );
    replaceMediaPlaceholder(conversationId, tempPlaceholderId, errorMessage);
    onMessageSent(errorMessage);
    onMediaTaskSubmitted?.();
  }
}
```

---

## 八、调用方式示例

### 8.1 首次发送（Handler 改造后）

```typescript
// useTextMessageHandler.ts 改造后
import { sendChatMessage } from '@/services/messageSender';

const handleChatMessage = async (
  content: string,
  conversationId: string,
  imageUrl?: string | null
) => {
  await sendChatMessage({
    type: 'chat',
    conversationId,
    content,
    imageUrl,
    modelId: selectedModel.id,
    thinkingEffort,
    deepThinkMode,
    callbacks: {
      onMessagePending,
      onMessageSent,
      onStreamContent,
      onStreamStart,
    },
  });
};
```

### 8.2 成功重新生成（直接复用）

```typescript
// 成功重新生成图片 → 直接调用 sendMessage
import { sendMessage } from '@/services/messageSender';

const regenerateSuccessImage = async (userMessage: Message) => {
  const imageParams = userMessage.generation_params?.image;

  await sendMessage({
    type: 'image',
    conversationId,
    content: userMessage.content,
    imageUrl: userMessage.image_url,
    modelId: imageParams?.model || selectedModel.id,
    generationParams: {
      image: {
        aspectRatio: imageParams?.aspectRatio || savedSettings.image.aspectRatio,
        outputFormat: imageParams?.outputFormat || savedSettings.image.outputFormat,
        resolution: imageParams?.resolution || savedSettings.image.resolution,
        model: imageParams?.model || selectedModel.id,
      },
    },
    conversationTitle,
    callbacks: {
      onMessagePending,
      onMessageSent,
      onMediaTaskSubmitted,
    },
  });
};
```

---

## 九、改造影响范围

| 文件 | 改动说明 |
|-----|---------|
| `useTextMessageHandler.ts` | 改为调用 `sendChatMessage` |
| `useImageMessageHandler.ts` | 改为调用 `sendImageMessage` |
| `useVideoMessageHandler.ts` | 改为调用 `sendVideoMessage` |
| `useRegenerateAsNewMessage.ts` | 改为调用 `sendChatMessage` |
| `mediaRegeneration.ts` | `executeImageRegeneration` / `executeVideoRegeneration` 改为调用对应 sender |

---

## 十、后续持久化改动点

实现持久化时，只需修改以下 3 处：

| 改动点 | 说明 |
|-------|------|
| `messageSender/` 各 sender | 发送逻辑（首次 + 成功重新生成都生效） |
| `regenerateInPlace.ts` | 失败重新生成逻辑 |
| 占位符组件 | 持久化占位符显示 |

---

## 十一、扩展新媒体类型

后续新增媒体类型（如音频、3D）只需：

1. **types.ts**：新增 `AudioSenderParams` 类型
2. **audioSender.ts**：新增发送器文件
3. **index.ts**：switch 新增 case（不加会编译报错）

```typescript
// 新增音频支持示例
export type MediaType = 'chat' | 'image' | 'video' | 'audio';

// index.ts switch 新增
case 'audio':
  return sendAudioMessage(params);
```

---

## 十二、验收标准

### 功能验收

- [ ] 首次发送聊天消息正常
- [ ] 首次发送图片消息正常
- [ ] 首次发送视频消息正常
- [ ] 成功重新生成聊天消息正常（复用发送逻辑）
- [ ] 成功重新生成图片消息正常（复用发送逻辑）
- [ ] 成功重新生成视频消息正常（复用发送逻辑）

### 代码质量验收

- [ ] TypeScript 编译无错误
- [ ] ESLint 无警告
- [ ] 无重复的发送逻辑代码

---

## 更新日志

| 日期 | 版本 | 说明 |
|-----|------|------|
| 2026-02-01 | v1.0 | 初始版本 |

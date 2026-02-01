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
import { sendMediaMessage } from './mediaSender';
import type { ChatSenderParams, ImageSenderParams, VideoSenderParams } from './types';

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
    case 'video':
      // 使用统一媒体发送器
      return sendMediaMessage(params);
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
 * 统一媒体发送器（图片/视频）
 */
export { sendMediaMessage } from './mediaSender';

/**
 * @deprecated 使用 sendMediaMessage 替代
 */
export { sendImageMessage } from './imageSender';

/**
 * @deprecated 使用 sendMediaMessage 替代
 */
export { sendVideoMessage } from './videoSender';

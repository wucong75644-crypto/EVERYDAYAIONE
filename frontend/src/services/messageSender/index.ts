/**
 * 消息发送服务统一导出
 *
 * 使用场景：
 * 1. 首次发送消息
 * 2. 成功消息重新生成
 *
 * 实际使用：
 * - 聊天消息使用 sendChatMessage
 * - 图片/视频消息使用 sendMediaMessage
 */

export * from './types';

/**
 * 发送聊天消息（支持流式响应）
 */
export { sendChatMessage } from './chatSender';

/**
 * 统一媒体发送器（图片/视频）
 */
export { sendMediaMessage } from './mediaSender';

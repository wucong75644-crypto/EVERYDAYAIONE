/**
 * 消息 ID 映射管理器
 *
 * 用于处理乐观更新时临时消息和真实消息的映射关系。
 *
 * **核心原理**：
 * 1. 前端发送消息时，生成唯一的 client_request_id（如 req-1706597123456-abc123）
 * 2. 创建临时消息（temp-xxx），立即显示给用户（使用本地预览 URL）
 * 3. 后端保存消息后返回真实消息（UUID），携带相同的 client_request_id
 * 4. 前端收到后，根据 client_request_id 匹配并替换临时消息
 *
 * **使用场景**：
 * - 聊天消息：使用本地预览 URL（blob://）立即显示，后端返回服务器 URL 后替换
 * - 避免消息重复显示（临时消息 + 真实消息）
 * - 支持消息状态追踪（pending → sent → failed）
 */

/**
 * 生成唯一的客户端请求 ID
 *
 * 格式：req-{timestamp}-{random}
 * 示例：req-1706597123456-abc123
 *
 * @returns 唯一的请求 ID
 */
export function generateClientRequestId(): string {
  const timestamp = Date.now();
  const random = Math.random().toString(36).substring(2, 9);
  return `req-${timestamp}-${random}`;
}

/**
 * 生成临时消息 ID
 *
 * 格式：temp-{timestamp}-{random}
 * 示例：temp-1706597123456-xyz789
 *
 * @returns 临时消息 ID
 */
export function generateTempMessageId(): string {
  const timestamp = Date.now();
  const random = Math.random().toString(36).substring(2, 9);
  return `temp-${timestamp}-${random}`;
}

/**
 * 检查是否为临时消息 ID
 *
 * @param id - 消息 ID
 * @returns 是否为临时 ID
 */
export function isTempMessageId(id: string): boolean {
  return id.startsWith('temp-');
}

/**
 * 检查是否为真实消息 ID（UUID格式）
 *
 * @param id - 消息 ID
 * @returns 是否为真实 ID
 */
export function isRealMessageId(id: string): boolean {
  // UUID 格式：xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  return uuidRegex.test(id);
}

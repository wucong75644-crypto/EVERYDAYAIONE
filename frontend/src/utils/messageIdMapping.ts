/**
 * 消息 ID 映射管理器
 *
 * 用于处理乐观更新时临时消息和真实消息的映射关系。
 *
 * **核心原理**：
 * 1. 前端发送消息时，生成唯一的 client_request_id（如 req-1706597123456-abc123）
 * 2. 创建临时消息，立即显示给用户
 * 3. 后端保存消息后返回真实消息（UUID），携带相同的 client_request_id
 * 4. 前端收到后，根据 client_request_id 匹配并替换临时消息
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

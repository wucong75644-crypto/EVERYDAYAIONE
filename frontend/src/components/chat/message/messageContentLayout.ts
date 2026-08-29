/**
 * 对话内容块的统一宽度契约。
 *
 * 内容块必须由容器宽度决定，而不是由模型输出的文字长度决定：
 * - standard：表单、表格、结果等需要完整阅读空间的内容；
 * - compact：工具调用、思考步骤等辅助信息。
 */
export const MESSAGE_CONTENT_LAYOUT = {
  standard: 'w-full min-w-0 max-w-[960px]',
  compact: 'w-full min-w-0 max-w-[640px]',
  fill: 'w-full min-w-0',
} as const;

/** 消息区与输入框共用横向边界；内容块跟随父容器。 */
export function chatColumnLayout(compact = false): string {
  return `${compact ? '' : 'max-w-4xl mx-auto '}w-full min-w-0 px-4`;
}

/** 对齐规则只由消息角色决定，文件与媒体列表共用。 */
export const MESSAGE_ALIGNMENT = {
  user: 'justify-end',
  assistant: 'justify-start',
} as const;

export const MESSAGE_CONTENT_LAYOUT = {
  standard: 'w-full min-w-0',
  compact: 'w-full min-w-0 max-w-[500px]',
  fill: 'w-full min-w-0',
} as const;

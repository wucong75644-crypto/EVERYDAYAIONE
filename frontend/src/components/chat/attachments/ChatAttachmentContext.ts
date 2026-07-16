import { createContext, useContext } from 'react';
import type { useChatAttachments } from './useChatAttachments';

export type ChatAttachmentController = ReturnType<typeof useChatAttachments>;

export const ChatAttachmentContext = createContext<ChatAttachmentController | null>(null);

export function useChatAttachmentContext(): ChatAttachmentController {
  const value = useContext(ChatAttachmentContext);
  if (!value) throw new Error('useChatAttachmentContext 必须在 ChatAttachmentProvider 内使用');
  return value;
}

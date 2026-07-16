import type { ReactNode } from 'react';
import { ChatAttachmentContext } from './ChatAttachmentContext';
import { useChatAttachments } from './useChatAttachments';

export function ChatAttachmentProvider({ children }: { children: ReactNode }) {
  const controller = useChatAttachments();
  return (
    <ChatAttachmentContext.Provider value={controller}>
      {children}
    </ChatAttachmentContext.Provider>
  );
}

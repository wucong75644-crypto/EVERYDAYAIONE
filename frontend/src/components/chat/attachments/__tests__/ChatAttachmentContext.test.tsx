import { render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ChatAttachmentProvider } from '../ChatAttachmentProvider';
import { useChatAttachmentContext } from '../ChatAttachmentContext';

const controller = vi.hoisted(() => ({ attachments: [] }));
vi.mock('../useChatAttachments', () => ({ useChatAttachments: () => controller }));

describe('ChatAttachmentContext', () => {
  it('Provider 向聊天子树提供唯一附件控制器', () => {
    function Consumer() {
      const value = useChatAttachmentContext();
      return <span>{value === controller ? 'connected' : 'disconnected'}</span>;
    }
    render(<ChatAttachmentProvider><Consumer /></ChatAttachmentProvider>);
    expect(screen.getByText('connected')).toBeInTheDocument();
  });

  it('Provider 外使用时明确报错', () => {
    expect(() => renderHook(() => useChatAttachmentContext())).toThrow(
      'useChatAttachmentContext 必须在 ChatAttachmentProvider 内使用',
    );
  });
});

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { FailedMediaPlaceholder } from '../media/MediaPlaceholder';
import MessageBubbleContent from '../message/MessageBubbleContent';
import type { Message } from '../../../stores/useMessageStore';

describe('媒体生成中文字', () => {
  it('pending 图片空内容优先显示生成中文字而不是已取消', () => {
    const message: Message = {
      id: 'message-1', conversation_id: 'conversation-1', role: 'assistant',
      content: [], status: 'pending', created_at: '2026-07-22T00:00:00Z',
      generation_params: { type: 'image' },
    };
    render(
      <MessageBubbleContent
        message={message}
        isUser={false}
        hasMultiBlocks={false}
        imageAssets={[]}
        fileBlocks={[]}
        isStreaming={false}
        isRegenerating={false}
        textContent=""
        hasImage={false}
        hasVideo={false}
        hasFiles={false}
        isErrorMessage={false}
        bubbleTextInfo={{ text: '图片生成中', hasAnimation: true }}
        onImageClick={vi.fn()}
      />,
    );

    expect(screen.getByText('图片生成中')).toBeInTheDocument();
    expect(screen.queryByText(/已取消/)).not.toBeInTheDocument();
  });
});

describe('FailedMediaPlaceholder', () => {
  it('积分不足时显示固定文案和警告图标', () => {
    render(
      <FailedMediaPlaceholder
        type="image"
        width={512}
        height={512}
        errorMessage="provider raw error"
        errorCode="INSUFFICIENT_CREDITS"
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('积分不足');
    expect(screen.queryByText('provider raw error')).not.toBeInTheDocument();
    expect(document.querySelector('.lucide-triangle-alert')).toBeInTheDocument();
  });

  it('积分不足仍保留重新生成按钮', () => {
    const onRetry = vi.fn();
    render(
      <FailedMediaPlaceholder
        type="image"
        aspectRatio={1}
        errorCode="INSUFFICIENT_CREDITS"
        onRetry={onRetry}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('普通失败保持原错误文案和裂图图标', () => {
    render(
      <FailedMediaPlaceholder
        type="image"
        width={512}
        height={512}
        errorMessage="模型超时"
        errorCode="MODEL_TIMEOUT"
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('模型超时');
    expect(document.querySelector('.lucide-image-off')).toBeInTheDocument();
  });
});

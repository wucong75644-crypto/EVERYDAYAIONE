import type { ImageAsset, Message } from '../../../stores/useMessageStore';
import type { FilePart } from '../../../types/message';
import LoadingPlaceholder from './LoadingPlaceholder';
import MarkdownRenderer from './MarkdownRenderer';
import MessageContentBlocks from './MessageContentBlocks';
import ThinkingBlock from './ThinkingBlock';

interface BubbleTextInfo {
  text: string;
  hasAnimation: boolean;
}

interface MessageBubbleContentProps {
  message: Message;
  isUser: boolean;
  hasMultiBlocks: boolean;
  imageAssets: ImageAsset[];
  fileBlocks: FilePart[];
  isStreaming: boolean;
  isRegenerating: boolean;
  textContent: string;
  thinkingContent?: string;
  hasImage: boolean;
  hasVideo: boolean;
  hasFiles: boolean;
  isErrorMessage: boolean;
  suggestions?: string[];
  bubbleTextInfo: BubbleTextInfo | null;
  agentStepHint?: string;
  streamingThinking?: string;
  thinkingStartTime?: number;
  onImageClick: (index?: number) => void;
  onRegenerateSingle?: (imageIndex: number) => void;
}

export default function MessageBubbleContent({
  message,
  isUser,
  hasMultiBlocks,
  imageAssets,
  fileBlocks,
  isStreaming,
  isRegenerating,
  textContent,
  thinkingContent,
  hasImage,
  hasVideo,
  hasFiles,
  isErrorMessage,
  suggestions,
  bubbleTextInfo,
  agentStepHint,
  streamingThinking,
  thinkingStartTime,
  onImageClick,
  onRegenerateSingle,
}: MessageBubbleContentProps) {
  const thinkingFromContent = !hasMultiBlocks
    ? message.content.find(p => p.type === 'thinking') as import('../../../types/message').ThinkingPart | undefined
    : undefined;
  const thinkingText = streamingThinking || thinkingFromContent?.text || thinkingContent || '';
  const thinkingDurationMs = thinkingFromContent?.duration_ms;
  const isThinkingNow = !!(isStreaming && !thinkingFromContent && !textContent);
  const shouldShowSingleThinking = !isUser
    && !hasMultiBlocks
    && (thinkingText || isThinkingNow || thinkingDurationMs != null);

  return (
    <>
      {shouldShowSingleThinking && (
        <ThinkingBlock
          content={thinkingText}
          isThinking={isThinkingNow}
          thinkingStartTime={thinkingStartTime}
          durationMs={thinkingDurationMs}
        />
      )}

      <div className={isUser ? 'text-[15px] leading-relaxed whitespace-pre-wrap' : ''}>
        {((isRegenerating || isStreaming) && !textContent && !hasMultiBlocks) ? (
          <LoadingPlaceholder text={agentStepHint || 'AI 正在思考'} />
        ) : (!isUser && !textContent && !hasImage && !hasVideo && !hasFiles && !hasMultiBlocks && !isErrorMessage && !isStreaming && !isRegenerating && !(suggestions && suggestions.length > 0)) ? (
          <span className="text-text-disabled text-sm italic">已取消，点击「重新生成」重试</span>
        ) : bubbleTextInfo ? (
          bubbleTextInfo.hasAnimation ? (
            <LoadingPlaceholder text={bubbleTextInfo.text} />
          ) : (
            <span>{bubbleTextInfo.text}</span>
          )
        ) : isErrorMessage ? (
          <span className="text-[15px]">{textContent || 'Error occurred'}</span>
        ) : isUser ? (
          <>{textContent}</>
        ) : hasMultiBlocks ? (
          <MessageContentBlocks
            message={message}
            imageAssets={imageAssets}
            fileBlocks={fileBlocks}
            isStreaming={isStreaming}
            isRegenerating={isRegenerating}
            textContent={textContent}
            agentStepHint={agentStepHint}
            streamingThinking={streamingThinking}
            thinkingStartTime={thinkingStartTime}
            onImageClick={onImageClick}
            onRegenerateSingle={onRegenerateSingle}
          />
        ) : (
          <MarkdownRenderer content={textContent} />
        )}
      </div>

      {!isUser && (isStreaming || isRegenerating) && textContent && !hasMultiBlocks && (
        <div className="mt-1.5">
          <LoadingPlaceholder text={agentStepHint || 'AI 正在输出'} />
        </div>
      )}
    </>
  );
}

import { lazy, Suspense } from 'react';
import toast from 'react-hot-toast';
import { formatRelativeCN } from '../../../utils/formatRelativeCN';
import api from '../../../services/api';
import { useMessageStore, type Message } from '../../../stores/useMessageStore';
import { resolveImageOriginalUrl } from '../../../utils/messageUtils';
import type { FilePart, ImageAsset, ImagePart } from '../../../types/message';
import { FailedMediaPlaceholder } from '../media/MediaPlaceholder';
import FileCardList from '../media/FileCard';
import ChartBlock from './ChartBlock';
import EcomPlanBlock from './EcomPlanBlock';
import FormBlock from './FormBlock';
import InlineChartImage from './InlineChartImage';
import LoadingPlaceholder from './LoadingPlaceholder';
import MarkdownRenderer from './MarkdownRenderer';
import { TableBlock } from './TableBlock';
import ThinkingBlock from './ThinkingBlock';
import ToolResultBlock from './ToolResultBlock';
import ToolStepCard from './ToolStepCard';

const DiagramBlock = lazy(() => import('./DiagramBlock'));

interface MessageContentBlocksProps {
  message: Message;
  imageAssets: ImageAsset[];
  fileBlocks: FilePart[];
  isStreaming: boolean;
  isRegenerating: boolean;
  textContent: string;
  agentStepHint?: string;
  streamingThinking?: string;
  thinkingStartTime?: number;
  onImageClick: (index?: number) => void;
  onRegenerateSingle?: (imageIndex: number) => void;
}

export default function MessageContentBlocks({
  message,
  imageAssets,
  fileBlocks,
  isStreaming,
  isRegenerating,
  textContent,
  agentStepHint,
  streamingThinking,
  thinkingStartTime,
  onImageClick,
  onRegenerateSingle,
}: MessageContentBlocksProps) {
  return (
    <div className="space-y-1">
      {message.content.map((part, idx) => {
        if (part.type === 'thinking') {
          const tp = part as { text?: string; duration_ms?: number };
          if (!tp.text && tp.duration_ms == null) return null;
          return (
            <ThinkingBlock
              key={`thinking-${idx}`}
              content={tp.text || ''}
              durationMs={tp.duration_ms}
            />
          );
        }
        if (part.type === 'tool_step') {
          const ts = part as { tool_name: string; tool_call_id: string; status: 'running' | 'completed' | 'error' | 'cancelled'; code?: string; output?: string; input?: string; elapsed_ms?: number };
          return (
            <ToolStepCard
              key={ts.tool_call_id || idx}
              toolName={ts.tool_name || 'tool'}
              toolCallId={ts.tool_call_id || String(idx)}
              status={ts.status || 'completed'}
              code={ts.code}
              output={ts.output}
              input={ts.input}
              elapsedMs={ts.elapsed_ms}
            />
          );
        }
        if (part.type === 'interrupt_marker') return null;
        if (part.type === 'text' && (part as { text: string }).text) {
          return (
            <MarkdownRenderer
              key={idx}
              content={(part as { text: string }).text}
            />
          );
        }
        if (part.type === 'tool_result') {
          const tr = part as { tool_name: string; text: string; files?: Array<{ url: string; name: string; mime_type: string; size?: number }> };
          return (
            <ToolResultBlock
              key={idx}
              toolName={tr.tool_name}
              text={tr.text}
              files={tr.files}
            />
          );
        }
        if (part.type === 'image' && (part as { url?: string }).url) {
          const img = part as ImagePart;
          const originalUrl = resolveImageOriginalUrl(img) || img.url || '';
          if (!originalUrl) return null;
          const imgIndex = imageAssets.findIndex((asset) => asset.originalUrl === originalUrl);
          return (
            <InlineChartImage
              key={originalUrl}
              url={originalUrl}
              alt={img.alt || '生成的图表'}
              width={img.width}
              height={img.height}
              onClick={() => onImageClick(imgIndex >= 0 ? imgIndex : 0)}
            />
          );
        }
        if (part.type === 'image' && (part as { failed?: boolean }).failed) {
          const failedImg = part as {
            width?: number; height?: number; alt?: string;
            retry_context?: { task: string; image_urls: string[]; platform: string; style_directive: string };
          };
          const imageIdx = message.content
            .filter((p, i) => i < idx && p.type === 'image')
            .length;
          const ecomRetry = failedImg.retry_context ? async () => {
            try {
              const { data } = await api.post('/ecom-image/retry', {
                conversation_id: message.conversation_id,
                message_id: message.id,
                task: failedImg.retry_context!.task,
                image_urls: failedImg.retry_context!.image_urls,
                platform: failedImg.retry_context!.platform,
                style_directive: failedImg.retry_context!.style_directive,
                part_index: imageIdx,
              });
              if (data.success && data.image_url) {
                const newContent = [...message.content];
                newContent[idx] = {
                  type: 'image',
                  url: data.image_url,
                  width: failedImg.width || 800,
                  height: failedImg.height || 800,
                  alt: failedImg.alt || '',
                };
                useMessageStore.getState().updateMessage(message.id, { content: newContent });
                toast.success('图片重新生成成功');
              } else {
                toast.error(data.error || '重新生成失败');
              }
            } catch {
              toast.error('重新生成失败，请稍后再试');
            }
          } : undefined;
          const fallbackRetry = !ecomRetry && onRegenerateSingle
            ? () => onRegenerateSingle(imageIdx)
            : undefined;
          return (
            <div key={`failed-${idx}`} className="my-2">
              <FailedMediaPlaceholder
                type="image"
                width={failedImg.width || 280}
                height={failedImg.height || 280}
                retryLabel="重新生成"
                onRetry={ecomRetry || fallbackRetry}
              />
            </div>
          );
        }
        if (part.type === 'file') return null;
        if (part.type === 'chart') {
          const cp = part as import('../../../types/message').ChartPart;
          return (
            <div key={idx} className="my-3 group" style={{ maxWidth: '100%' }}>
              <ChartBlock
                option={cp.option}
                title={cp.title}
                spec_format={cp.spec_format}
                messageId={message.id}
              />
            </div>
          );
        }
        if (part.type === 'diagram') {
          return (
            <Suspense
              key={`diagram-${idx}`}
              fallback={<div className="my-3 p-4 text-sm text-text-disabled">关系图组件加载中...</div>}
            >
              <DiagramBlock diagram={part} messageId={message.id} />
            </Suspense>
          );
        }
        if (part.type === 'table') {
          const tp = part as import('../../../types/message').TablePart;
          return (
            <TableBlock
              key={idx}
              title={tp.title}
              columns={tp.columns}
              rows={tp.rows}
              truncated={tp.truncated}
            />
          );
        }
        if (part.type === 'form') {
          const fp = part as import('../../../types/message').FormPart;
          return <FormBlock key={fp.form_id} form={fp} />;
        }
        if (part.type === 'ecom_plan') {
          const ep = part as import('../../../types/message').EcomPlanPart;
          return (
            <EcomPlanBlock
              key={`ecom-plan-${idx}`}
              plan={ep}
              onConfirm={(images) => {
                window.dispatchEvent(new CustomEvent('ecom:confirm-generate', {
                  detail: { images, conversationId: message.conversation_id },
                }));
              }}
            />
          );
        }
        return null;
      })}
      {message.status === 'interrupted' && (() => {
        const marker = message.content.find(
          (p) => p.type === 'interrupt_marker'
        ) as { interrupted_at?: string } | undefined;
        if (!marker?.interrupted_at) return null;
        const ago = formatRelativeCN(marker.interrupted_at);
        return (
          <div
            className="mt-1 text-[10px] text-text-tertiary leading-none"
            data-testid="interrupt-hint"
          >
            停止于 {ago}
          </div>
        );
      })()}
      {!isStreaming && fileBlocks.length > 0 && (
        <div className="my-2" style={{ maxWidth: '400px' }}>
          <FileCardList files={fileBlocks} />
        </div>
      )}
      {isStreaming && (() => {
        const lastBlock = message.content[message.content.length - 1];
        if (lastBlock?.type === 'text') return null;
        if (lastBlock?.type === 'thinking') return null;
        const committedLen = message.content
          .filter(p => p.type === 'thinking')
          .reduce((sum, p) => sum + ((p as { text?: string }).text?.length || 0), 0);
        const livePart = (streamingThinking || '').slice(committedLen);
        return (
          <ThinkingBlock
            content={livePart}
            isThinking
            thinkingStartTime={thinkingStartTime}
          />
        );
      })()}
      {(isStreaming || isRegenerating) && (
        <LoadingPlaceholder text={agentStepHint || (
          textContent ? 'AI 正在输出' : 'AI 正在思考'
        )} />
      )}
    </div>
  );
}

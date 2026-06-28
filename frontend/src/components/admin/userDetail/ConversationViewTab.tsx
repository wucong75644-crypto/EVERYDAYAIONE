/**
 * 会话视图 Tab — 左对话列表 + 右消息流（按聊天记录顺序展示）
 *
 * 不复用 MessageItem（深耦合 4 个全局 store），新写简化版 AdminMessageBubble。
 * 用户消息附件 + AI 生图 + 提示词 全部按时间顺序展示。
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import { Copy, Download } from 'lucide-react';
import { Button } from '../../ui/Button';
import {
  listUserConversations,
  getUserConversationMessages,
  downloadUserAssetsZip,
  type ConversationListItem,
  type ConversationMessage,
} from '../../../services/adminUser';
import { formatRelativeCN } from '../../../utils/formatRelativeCN';
import { downloadFile } from '../../../utils/downloadFile';

interface Props {
  userId: string;
}

export default function ConversationViewTab({ userId }: Props) {
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [convLoading, setConvLoading] = useState(true);
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);

  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [msgLoading, setMsgLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);

  // 加载对话列表
  useEffect(() => {
    (async () => {
      setConvLoading(true);
      try {
        const data = await listUserConversations(userId, { page: 1, page_size: 50 });
        setConversations(data.items);
        if (data.items.length > 0) {
          setSelectedConvId(data.items[0].id);
        }
      } catch (err: any) {
        toast.error(err?.response?.data?.detail || '加载对话失败');
      } finally {
        setConvLoading(false);
      }
    })();
  }, [userId]);

  // 加载选中对话的消息
  const loadMessages = useCallback(async (cid: string) => {
    setMsgLoading(true);
    try {
      const data = await getUserConversationMessages(userId, cid);
      setMessages(data.items);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || '加载消息失败');
      setMessages([]);
    } finally {
      setMsgLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    if (selectedConvId) loadMessages(selectedConvId);
  }, [selectedConvId, loadMessages]);

  // 收集本对话所有可下载 URL
  const allUrls = useMemo(() => {
    const urls: string[] = [];
    for (const m of messages) {
      m.attachments?.forEach((a) => urls.push(a.url));
      if (m.image_url) urls.push(m.image_url);
      if (m.video_url) urls.push(m.video_url);
    }
    return urls;
  }, [messages]);

  const handleDownloadAll = async () => {
    if (allUrls.length === 0) {
      toast.error('本对话没有可下载素材');
      return;
    }
    setDownloading(true);
    try {
      const conv = conversations.find((c) => c.id === selectedConvId);
      const safeTitle = (conv?.title || 'conversation').replace(/[\\/:*?"<>|]/g, '_').slice(0, 50);
      await downloadUserAssetsZip(userId, {
        urls: allUrls,
        zip_name: `${safeTitle}.zip`,
      });
      toast.success('下载已开始');
    } catch (err: any) {
      toast.error(err?.message || '下载失败');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="flex gap-4 h-full -m-6">
      {/* 左：对话列表 */}
      <div className="w-64 border-r border-[var(--s-border-default)] overflow-y-auto shrink-0">
        {convLoading ? (
          <div className="text-center py-6 text-[var(--s-text-tertiary)] text-sm">加载中...</div>
        ) : conversations.length === 0 ? (
          <div className="text-center py-6 text-[var(--s-text-tertiary)] text-sm">暂无对话</div>
        ) : (
          conversations.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setSelectedConvId(c.id)}
              className={`w-full text-left px-3 py-2.5 border-b border-[var(--s-border-default)]/60 hover:bg-[var(--s-hover)] transition-colors ${
                selectedConvId === c.id ? 'bg-[var(--s-accent)]/10 border-l-2 border-l-[var(--s-accent)]' : ''
              }`}
            >
              <div className="text-sm font-medium truncate">{c.title || '未命名'}</div>
              <div className="text-xs text-[var(--s-text-tertiary)] flex justify-between mt-0.5">
                <span>{c.message_count} 条</span>
                <span>{formatRelativeCN(c.updated_at)}</span>
              </div>
            </button>
          ))
        )}
      </div>

      {/* 右：消息流 */}
      <div className="flex-1 flex flex-col min-w-0">
        {selectedConvId && (
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--s-border-default)]">
            <div className="text-xs text-[var(--s-text-tertiary)]">
              {allUrls.length > 0 ? `共 ${allUrls.length} 个素材` : '无素材'}
            </div>
            <Button
              size="sm"
              variant="ghost"
              icon={<Download className="w-3.5 h-3.5" />}
              disabled={allUrls.length === 0}
              loading={downloading}
              onClick={handleDownloadAll}
            >
              下载本对话全部素材
            </Button>
          </div>
        )}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {msgLoading ? (
            <div className="text-center py-12 text-[var(--s-text-tertiary)] text-sm">加载中...</div>
          ) : messages.length === 0 ? (
            <div className="text-center py-12 text-[var(--s-text-tertiary)] text-sm">
              {selectedConvId ? '本对话无消息' : '请从左侧选择一个对话'}
            </div>
          ) : (
            messages.map((m) => <AdminMessageBubble key={m.id} message={m} />)
          )}
        </div>
      </div>
    </div>
  );
}


// ── 简化版消息气泡（admin 场景专用）────────────────────


function AdminMessageBubble({ message }: { message: ConversationMessage }) {
  const isUser = message.role === 'user';
  const textContent = useMemo(() => extractText(message), [message]);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('已复制');
  };

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className={`w-8 h-8 rounded-full shrink-0 flex items-center justify-center text-xs font-medium ${
        isUser
          ? 'bg-[var(--s-accent)] text-white'
          : 'bg-[var(--s-bg-tertiary)] text-[var(--s-text-primary)]'
      }`}>
        {isUser ? '我' : 'AI'}
      </div>
      <div className={`flex-1 min-w-0 ${isUser ? 'flex flex-col items-end' : ''}`}>
        <div className={`text-xs text-[var(--s-text-tertiary)] mb-1 ${isUser ? 'text-right' : ''}`}>
          {message.role} · {formatRelativeCN(message.created_at)}
        </div>

        <div className={`max-w-[90%] rounded-lg px-3 py-2 ${
          isUser
            ? 'bg-[var(--s-accent)]/10 border border-[var(--s-accent)]/20'
            : 'bg-[var(--s-bg-secondary)]'
        }`}>
          {/* 文本 */}
          {textContent && (
            <div className="text-sm whitespace-pre-wrap break-words">{textContent}</div>
          )}

          {/* 用户上传附件 */}
          {message.attachments && message.attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-2">
              {message.attachments.map((a, i) => (
                <AttachmentThumb key={i} url={a.url} name={a.name} type={a.type} />
              ))}
            </div>
          )}

          {/* AI 生成图 */}
          {message.image_url && (
            <div className="mt-2">
              <AttachmentThumb url={message.image_url} name="生成图" type="image" />
            </div>
          )}

          {/* AI 生成视频 */}
          {message.video_url && (
            <div className="mt-2">
              <a
                href={message.video_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-[var(--s-accent)] underline"
              >
                查看视频 →
              </a>
            </div>
          )}
        </div>

        {/* 提示词（来自 generation_params） */}
        {!isUser && message.generation_params && (
          <PromptHint params={message.generation_params} onCopy={handleCopy} />
        )}

        {/* 积分消耗 */}
        {message.credits_cost > 0 && (
          <div className="text-xs text-[var(--s-text-tertiary)] mt-1">
            消耗 {message.credits_cost} 积分
          </div>
        )}
      </div>
    </div>
  );
}


function AttachmentThumb({ url, name, type }: { url: string; name: string; type: 'file' | 'image' }) {
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    downloadFile(url, name).catch((err) => toast.error(err?.message || '下载失败'));
  };
  if (type === 'image') {
    return (
      <div className="relative group">
        <img
          src={url}
          alt={name}
          className="w-24 h-24 rounded object-cover cursor-pointer"
          onClick={() => window.open(url, '_blank')}
        />
        <button
          type="button"
          onClick={handleClick}
          className="absolute top-1 right-1 p-1 bg-black/60 hover:bg-black/80 text-white rounded opacity-0 group-hover:opacity-100 transition-opacity"
          aria-label="下载"
        >
          <Download className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      className="text-sm px-2 py-1 bg-[var(--s-bg-tertiary)] rounded hover:bg-[var(--s-hover)] flex items-center gap-1.5"
    >
      📎 {name}
      <Download className="w-3 h-3 text-[var(--s-text-tertiary)]" />
    </button>
  );
}


function PromptHint({
  params,
  onCopy,
}: {
  params: Record<string, unknown>;
  onCopy: (text: string) => void;
}) {
  const prompt = extractPrompt(params);
  if (!prompt) return null;
  return (
    <div className="mt-1.5 max-w-[90%]">
      <div className="bg-[var(--s-bg-tertiary)]/60 border border-dashed border-[var(--s-border-default)] rounded p-2 text-xs">
        <div className="flex items-center justify-between gap-2 mb-1">
          <span className="text-[var(--s-text-tertiary)]">提示词</span>
          <button
            type="button"
            onClick={() => onCopy(prompt)}
            className="text-[var(--s-text-tertiary)] hover:text-[var(--s-text-primary)]"
            aria-label="复制提示词"
            title="复制"
          >
            <Copy className="w-3 h-3" />
          </button>
        </div>
        <div className="text-[var(--s-text-secondary)] line-clamp-3 break-words">{prompt}</div>
      </div>
    </div>
  );
}


// ── 工具 ─────────────────────────────────────────────────


function extractText(m: ConversationMessage): string {
  const parsed = m.content_parsed;
  if (Array.isArray(parsed)) {
    const texts: string[] = [];
    for (const p of parsed) {
      if (p && typeof p === 'object' && 'type' in p) {
        const part = p as Record<string, unknown>;
        if (part.type === 'text' && typeof part.text === 'string') {
          texts.push(part.text);
        }
      }
    }
    return texts.join('\n');
  }
  return typeof m.content === 'string' ? m.content : '';
}


function extractPrompt(params: Record<string, unknown>): string | null {
  const candidates = ['prompt', 'user_prompt', 'system_prompt', 'description'];
  for (const k of candidates) {
    const v = params[k];
    if (typeof v === 'string' && v.trim()) return v;
  }
  return null;
}

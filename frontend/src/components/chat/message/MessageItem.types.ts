import type { ImageAsset, Message } from '../../../stores/useMessageStore';

export interface MessageItemProps {
  message: Message;
  /** 是否正在流式输出 */
  isStreaming?: boolean;
  /** 是否正在重新生成 */
  isRegenerating?: boolean;
  /** 重新生成回调 */
  onRegenerate?: (messageId: string) => void;
  /** 删除回调 */
  onDelete?: (messageId: string) => void;
  /** 媒体加载完成回调（用于滚动调整） */
  onMediaLoaded?: () => void;
  /** 所有图片资产列表（主体预览用原图，缩略条用 thumbnailUrl） */
  allImageAssets?: ImageAsset[];
  /** 当前图片在列表中的索引（用于缩略图预览） */
  currentImageIndex?: number;
  /** 是否跳过进入动画（批量加载历史消息时） */
  skipEntryAnimation?: boolean;
  /** 单图重新生成回调（多图模式） */
  onRegenerateSingle?: (messageId: string, imageIndex: number) => void;
  /** Agent Loop 步骤提示（"正在搜索..." 等） */
  agentStepHint?: string;
  /** 流式思考内容 */
  streamingThinking?: string;
  /** 思考开始时间戳 */
  thinkingStartTime?: number;
  /** 是否启用 framer layout 动画（长对话时父级会禁用以保性能） */
  enableLayoutAnimation?: boolean;
  /** 建议问题列表（仅最后一条 AI 消息显示） */
  suggestions?: string[];
}

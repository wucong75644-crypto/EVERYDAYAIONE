/**
 * 多标签页实时同步管理器
 *
 * 使用 BroadcastChannel API 实现同源标签页间的实时通信
 * 解决：
 * - 标签页 A 完成聊天，标签页 B 不知道
 * - 多标签页重复恢复同一任务
 * - 积分变化不同步
 *
 * 【重要】同源限制：
 * - BroadcastChannel 只能在 **同源（Same-origin）** 标签页间通信
 * - 同源 = 协议 + 域名 + 端口 完全一致
 * - 例：chat.domain.com 和 app.domain.com 是不同源，无法通信
 * - 跨子域名场景：需使用 localStorage fallback 或后端 WebSocket 广播
 * - 当前实现已包含 localStorage fallback，覆盖绝大多数场景
 */

const CHANNEL_NAME = 'everydayai-sync';

export type TabSyncEventType =
  | 'chat_started'      // 聊天开始（用于显示"正在输入"）
  | 'chat_completed'    // 聊天完成（刷新消息列表）
  | 'chat_failed'       // 聊天失败
  | 'task_restored'     // 任务已被恢复（防止重复恢复）
  | 'message_updated'   // 消息更新（重新生成等）
  | 'credits_changed'   // 积分变化
  | 'conversation_deleted'; // 对话删除

interface TabSyncPayload {
  conversationId?: string;
  taskId?: string;
  messageId?: string;
  credits?: number;
  [key: string]: unknown;
}

interface TabSyncEvent {
  type: TabSyncEventType;
  payload: TabSyncPayload;
  timestamp: number;
  tabId: string;
}

type EventCallback = (payload: TabSyncPayload) => void;

class TabSyncManager {
  private channel: BroadcastChannel | null = null;
  private tabId: string;
  private listeners: Map<TabSyncEventType, Set<EventCallback>>;
  private isInitialized: boolean = false;
  private useFallback: boolean = false;
  private readonly STORAGE_KEY = 'everydayai-sync-event';

  constructor() {
    this.tabId = `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.listeners = new Map();
    this.init();
  }

  private init() {
    if (typeof window === 'undefined') return;

    // 优先使用 BroadcastChannel
    if ('BroadcastChannel' in window) {
      try {
        this.channel = new BroadcastChannel(CHANNEL_NAME);
        this.channel.onmessage = this.handleMessage.bind(this);
        this.isInitialized = true;
        return;
      } catch (error) {
        console.warn('[TabSync] BroadcastChannel failed, falling back to localStorage:', error);
      }
    }

    // 【降级兜底】使用 StorageEvent（监听 localStorage 变化）
    try {
      this.useFallback = true;
      window.addEventListener('storage', this.handleStorageEvent.bind(this));
      this.isInitialized = true;
      console.info('[TabSync] Using localStorage fallback');
    } catch (error) {
      console.error('[TabSync] Failed to initialize fallback:', error);
    }
  }

  /**
   * 处理 localStorage 变化事件（fallback 模式）
   */
  private handleStorageEvent(event: StorageEvent) {
    if (event.key !== this.STORAGE_KEY || !event.newValue) return;

    try {
      const data = JSON.parse(event.newValue) as TabSyncEvent;
      // StorageEvent 只会在其他标签页触发，所以不需要检查 tabId
      this.dispatchToListeners(data.type, data.payload);
    } catch (error) {
      console.error('[TabSync] Failed to parse storage event:', error);
    }
  }

  /**
   * 广播事件给其他标签页
   */
  broadcast(type: TabSyncEventType, payload: TabSyncPayload = {}) {
    if (!this.isInitialized) return;

    const event: TabSyncEvent = {
      type,
      payload,
      timestamp: Date.now(),
      tabId: this.tabId,
    };

    try {
      if (this.useFallback) {
        // 【fallback 模式】通过 localStorage 广播
        // 写入后立即删除，触发其他标签页的 storage 事件
        localStorage.setItem(this.STORAGE_KEY, JSON.stringify(event));
        localStorage.removeItem(this.STORAGE_KEY);
      } else if (this.channel) {
        // 【正常模式】通过 BroadcastChannel 广播
        this.channel.postMessage(event);
      }
    } catch (error) {
      console.error('[TabSync] Failed to broadcast:', error);
    }
  }

  /**
   * 监听特定事件
   * @returns 取消监听函数
   */
  on(type: TabSyncEventType, callback: EventCallback): () => void {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set());
    }
    this.listeners.get(type)!.add(callback);

    return () => {
      this.listeners.get(type)?.delete(callback);
    };
  }

  /**
   * 监听多个事件
   */
  onMany(
    events: Partial<Record<TabSyncEventType, EventCallback>>
  ): () => void {
    const unsubscribes = Object.entries(events).map(([type, callback]) =>
      this.on(type as TabSyncEventType, callback!)
    );

    return () => unsubscribes.forEach((unsub) => unsub());
  }

  /**
   * 处理来自其他标签页的消息（BroadcastChannel 模式）
   */
  private handleMessage(event: MessageEvent<TabSyncEvent>) {
    const { type, payload, tabId } = event.data;

    // 忽略自己发送的消息
    if (tabId === this.tabId) return;

    this.dispatchToListeners(type, payload);
  }

  /**
   * 分发事件给所有监听者
   */
  private dispatchToListeners(type: TabSyncEventType, payload: TabSyncPayload) {
    const listeners = this.listeners.get(type);
    if (listeners && listeners.size > 0) {
      listeners.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error(`[TabSync] Listener error for ${type}:`, error);
        }
      });
    }
  }

  /**
   * 获取当前标签页 ID
   */
  getTabId(): string {
    return this.tabId;
  }

  /**
   * 销毁（页面卸载时调用）
   */
  destroy() {
    if (this.channel) {
      this.channel.close();
      this.channel = null;
    }
    this.listeners.clear();
    this.isInitialized = false;
  }
}

// 全局单例
export const tabSync = new TabSyncManager();

// 页面卸载时清理
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => tabSync.destroy());
}

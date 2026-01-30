/**
 * 任务协调器 - 防止多个标签页同时轮询同一任务
 */

class TaskCoordinator {
  private channel: BroadcastChannel;
  private activeTasks = new Set<string>();
  private tabId: string;

  constructor() {
    this.tabId = this.getOrCreateTabId();
    this.channel = new BroadcastChannel('task-polling-coordinator');

    this.channel.onmessage = (event) => {
      if (event.data.type === 'task-started') {
        this.activeTasks.add(event.data.taskId);
      } else if (event.data.type === 'task-completed') {
        this.activeTasks.delete(event.data.taskId);
      }
    };

    setInterval(() => this.cleanupExpiredLocks(), 30000);
  }

  canStartPolling(taskId: string): boolean {
    const lockKey = `task-lock-${taskId}`;
    const lock = localStorage.getItem(lockKey);

    if (lock) {
      try {
        const lockData = JSON.parse(lock);
        const lockAge = Date.now() - lockData.timestamp;

        if (lockAge < 30000) {
          if (lockData.tabId === this.tabId) return true;
          return false;
        }
      } catch (e) {
        console.warn('解析任务锁失败:', e);
      }
    }

    localStorage.setItem(lockKey, JSON.stringify({
      timestamp: Date.now(),
      tabId: this.tabId,
    }));

    this.channel.postMessage({ type: 'task-started', taskId, tabId: this.tabId });
    this.activeTasks.add(taskId);

    return true;
  }

  releasePolling(taskId: string) {
    localStorage.removeItem(`task-lock-${taskId}`);
    this.activeTasks.delete(taskId);
    this.channel.postMessage({ type: 'task-completed', taskId, tabId: this.tabId });
  }

  renewLock(taskId: string) {
    const lockKey = `task-lock-${taskId}`;
    const lock = localStorage.getItem(lockKey);

    if (lock) {
      try {
        const lockData = JSON.parse(lock);
        if (lockData.tabId === this.tabId) {
          localStorage.setItem(lockKey, JSON.stringify({
            timestamp: Date.now(),
            tabId: this.tabId,
          }));
        }
      } catch (e) {
        console.warn('更新任务锁失败:', e);
      }
    }
  }

  private cleanupExpiredLocks() {
    const now = Date.now();

    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith('task-lock-')) {
        const lock = localStorage.getItem(key);
        if (lock) {
          try {
            const lockData = JSON.parse(lock);
            const lockAge = now - lockData.timestamp;

            if (lockAge > 60000) {
              localStorage.removeItem(key);
            }
          } catch {
            // JSON 解析失败，移除无效的锁
            localStorage.removeItem(key);
          }
        }
      }
    }
  }

  private getOrCreateTabId(): string {
    let tabId = sessionStorage.getItem('tab-id');
    if (!tabId) {
      tabId = `tab-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      sessionStorage.setItem('tab-id', tabId);
    }
    return tabId;
  }

  cleanup() {
    for (const taskId of this.activeTasks) {
      this.releasePolling(taskId);
    }
    this.channel.close();
  }
}

export const taskCoordinator = new TaskCoordinator();

window.addEventListener('beforeunload', () => {
  taskCoordinator.cleanup();
});

type SessionStoreName = 'memory' | 'messages' | 'subscriptions';

const resetters = new Map<SessionStoreName, () => void>();

export function registerSessionStoreReset(
  name: SessionStoreName,
  reset: () => void,
): void {
  resetters.set(name, reset);
}

export function resetSessionStores(): void {
  for (const reset of resetters.values()) {
    reset();
  }
}

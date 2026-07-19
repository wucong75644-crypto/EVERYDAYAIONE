import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  registerSessionStoreReset,
  resetSessionStores,
} from '../sessionStoreResetRegistry';

const memoryReset = vi.fn();
const messageReset = vi.fn();
const subscriptionReset = vi.fn();

beforeEach(() => {
  memoryReset.mockReset();
  messageReset.mockReset();
  subscriptionReset.mockReset();
  registerSessionStoreReset('memory', memoryReset);
  registerSessionStoreReset('messages', messageReset);
  registerSessionStoreReset('subscriptions', subscriptionReset);
});

describe('sessionStoreResetRegistry', () => {
  it('synchronously resets every loaded session store', () => {
    resetSessionStores();

    expect(memoryReset).toHaveBeenCalledOnce();
    expect(messageReset).toHaveBeenCalledOnce();
    expect(subscriptionReset).toHaveBeenCalledOnce();
  });

  it('replaces an existing store registration by name', () => {
    const replacement = vi.fn();
    registerSessionStoreReset('messages', replacement);

    resetSessionStores();

    expect(messageReset).not.toHaveBeenCalled();
    expect(replacement).toHaveBeenCalledOnce();
  });
});

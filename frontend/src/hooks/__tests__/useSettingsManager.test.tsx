import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatSettings } from '../../services/conversation';
import { getSavedSettings, saveSettings } from '../../utils/settingsStorage';
import { clearNewConversationSettings, getPendingConversationSettings, saveConversationSettings } from '../../utils/conversationSettingsPersistence';
import { useSettingsManager } from '../useSettingsManager';

const { update, notify } = vi.hoisted(() => ({ update: vi.fn(), notify: vi.fn() }));
vi.mock('../../services/conversation', () => ({ updateConversation: update }));
vi.mock('../../stores/useAuthStore', () => ({ useAuthStore: (select: (state: { user: { id: string } }) => unknown) => select({ user: { id: 'u1' } }) }));
vi.mock('react-hot-toast', () => ({ default: { error: notify } }));

let sequence = 0;
const conversation = () => `settings-test-${++sequence}`;
const square: ChatSettings = { image_aspect_ratio: '1:1', image_resolution: '1K', image_output_format: 'png' };
async function flush() {
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
}

describe('淘宝主图 settings persistence', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    clearNewConversationSettings('u1');
    update.mockReset().mockResolvedValue({});
    notify.mockClear();
  });
  afterEach(async () => {
    await flush();
    vi.useRealTimers();
  });

  it('restores saved true and false, while old conversations remain off despite an enabled default', () => {
    const defaults = getSavedSettings();
    saveSettings({ ...defaults, image: { ...defaults.image, taobaoMainImage: true } });
    const { result, rerender } = renderHook(({ id, settings }) => useSettingsManager(id, settings), {
      initialProps: { id: conversation(), settings: { ...square, image_taobao_main_image: true } as ChatSettings },
    });
    expect(result.current.imageSettings.taobaoMainImage).toBe(true);
    rerender({ id: conversation(), settings: { ...square, image_taobao_main_image: false } });
    expect(result.current.imageSettings.taobaoMainImage).toBe(false);
    rerender({ id: conversation(), settings: square });
    expect(result.current.imageSettings.taobaoMainImage).toBe(false);
  });

  it('journals immediately and restores after a refresh before debounce finishes', async () => {
    const id = conversation();
    const first = renderHook(() => useSettingsManager(id, square));
    act(() => first.result.current.setImageSetting('taobaoMainImage', true));
    expect(getPendingConversationSettings('u1', id)?.settings.image_taobao_main_image).toBe(true);
    expect(update).not.toHaveBeenCalled();
    first.unmount();
    const restored = renderHook(() => useSettingsManager(id, square));
    expect(restored.result.current.imageSettings.taobaoMainImage).toBe(true);
    await flush();
    expect(update).toHaveBeenLastCalledWith(id, { chat_settings: expect.objectContaining({ image_taobao_main_image: true }) });
    expect(getPendingConversationSettings('u1', id)).toBeNull();
  });

  it('retains selection and pending settings when save fails, then retries on reopen', async () => {
    const id = conversation();
    update.mockRejectedValueOnce(new Error('network failed'));
    const first = renderHook(() => useSettingsManager(id, square));
    act(() => first.result.current.setImageSetting('taobaoMainImage', true));
    await flush();
    expect(first.result.current.imageSettings.taobaoMainImage).toBe(true);
    expect(getPendingConversationSettings('u1', id)).not.toBeNull();
    expect(notify).toHaveBeenCalled();
    first.unmount();
    const restored = renderHook(() => useSettingsManager(id, square));
    expect(restored.result.current.imageSettings.taobaoMainImage).toBe(true);
    await flush();
    expect(getPendingConversationSettings('u1', id)).toBeNull();
  });

  it('preserves selection across resolution and format changes, and clears it atomically with non-square ratio', async () => {
    const id = conversation();
    const { result } = renderHook(() => useSettingsManager(id, square));
    act(() => {
      result.current.setImageSetting('taobaoMainImage', true);
      result.current.setImageSetting('resolution', '4K');
      result.current.setImageSetting('outputFormat', 'jpeg');
    });
    expect(result.current.imageSettings).toMatchObject({ taobaoMainImage: true, resolution: '4K', outputFormat: 'jpeg' });
    act(() => result.current.setImageSetting('aspectRatio', 'auto'));
    expect(result.current.imageSettings.taobaoMainImage).toBe(false);
    expect(getPendingConversationSettings('u1', id)?.settings).toMatchObject({ image_aspect_ratio: 'auto', image_taobao_main_image: false });
    await flush();
    expect(update).toHaveBeenCalledTimes(1);
  });

  it('ignores stale server settings after editing, but restores each conversation separately', () => {
    const id = conversation();
    const { result, rerender } = renderHook(({ id, settings }) => useSettingsManager(id, settings), {
      initialProps: { id, settings: square },
    });
    act(() => result.current.setImageSetting('taobaoMainImage', true));
    rerender({ id, settings: { ...square, image_taobao_main_image: false } });
    expect(result.current.imageSettings.taobaoMainImage).toBe(true);
    rerender({ id: conversation(), settings: square });
    expect(result.current.imageSettings.taobaoMainImage).toBe(false);
    rerender({ id, settings: square });
    expect(result.current.imageSettings.taobaoMainImage).toBe(true);
  });

  it('supports saving as default and persists reset to the current conversation', async () => {
    const id = conversation();
    const { result } = renderHook(() => useSettingsManager(id, square));
    act(() => result.current.setImageSetting('taobaoMainImage', true));
    act(() => result.current.saveSettings());
    expect(getSavedSettings().image.taobaoMainImage).toBe(true);
    const created = renderHook(() => useSettingsManager(null, null));
    expect(created.result.current.imageSettings.taobaoMainImage).toBe(true);
    created.unmount();
    act(() => result.current.resetSettings());
    expect(result.current.imageSettings.taobaoMainImage).toBe(false);
    expect(getSavedSettings().image.taobaoMainImage).toBe(false);
    await flush();
    expect(update).toHaveBeenLastCalledWith(id, { chat_settings: expect.objectContaining({ image_taobao_main_image: false }) });
  });

  it('serializes in-flight saves so the newest false wins and is not cleared by an older response', async () => {
    const id = conversation();
    let release!: () => void;
    update.mockImplementationOnce(() => new Promise<void>((resolve) => { release = resolve; }));
    saveConversationSettings('u1', id, { ...square, image_taobao_main_image: true });
    await flush();
    saveConversationSettings('u1', id, { ...square, image_taobao_main_image: false });
    await flush();
    expect(update).toHaveBeenCalledTimes(1);
    expect(getPendingConversationSettings('u1', id)?.settings.image_taobao_main_image).toBe(false);
    await act(async () => { release(); await Promise.resolve(); });
    expect(update).toHaveBeenCalledTimes(2);
    expect(update).toHaveBeenLastCalledWith(id, { chat_settings: expect.objectContaining({ image_taobao_main_image: false }) });
    expect(getPendingConversationSettings('u1', id)).toBeNull();
  });

  it('isolates local drafts by user and conversation', () => {
    const id = conversation();
    saveConversationSettings('u1', id, { image_taobao_main_image: true });
    expect(getPendingConversationSettings('u2', id)).toBeNull();
    expect(getPendingConversationSettings('u1', conversation())).toBeNull();
  });
  it('restores a new conversation draft after refresh without changing global defaults', () => {
    const first = renderHook(() => useSettingsManager(null, null));
    act(() => first.result.current.setImageSetting('taobaoMainImage', true));
    first.unmount();
    const restored = renderHook(() => useSettingsManager(null, null));
    expect(restored.result.current.imageSettings.taobaoMainImage).toBe(true);
    expect(getSavedSettings().image.taobaoMainImage).toBe(false);
    expect(update).not.toHaveBeenCalled();
  });

  it('keeps the draft during first-send creation and an older detail response', () => {
    const id = conversation();
    const { result, rerender } = renderHook(({ id, settings }: { id: string | null; settings: ChatSettings | null }) => useSettingsManager(id, settings), {
      initialProps: { id: null, settings: null },
    });
    act(() => result.current.setImageSetting('taobaoMainImage', true));
    const snapshot = getPendingConversationSettings('u1', 'new')!.settings;
    saveConversationSettings('u1', id, snapshot);
    rerender({ id, settings: null });
    expect(result.current.imageSettings.taobaoMainImage).toBe(true);
    expect(getPendingConversationSettings('u1', 'new')).toBeNull();
    rerender({ id, settings: { ...square, image_taobao_main_image: false } });
    expect(result.current.imageSettings.taobaoMainImage).toBe(true);
  });

});

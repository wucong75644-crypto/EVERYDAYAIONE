import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConversationList from '../ConversationList';
import { clearNewConversationSettings, saveConversationSettings } from '../../../../utils/conversationSettingsPersistence';

const { list } = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock('../../../../services/conversation', () => ({ getConversationList: list, updateConversation: vi.fn(), deleteConversation: vi.fn() }));
vi.mock('../../../../stores/useAuthStore', () => ({ useAuthStore: { getState: () => ({ user: { id: 'draft-owner' } }) } }));

const recent = { id: 'recent', title: '最近对话', model_id: null, last_message: '', updated_at: new Date().toISOString() };

describe('ConversationList refresh navigation', () => {
  beforeEach(() => {
    localStorage.clear();
    clearNewConversationSettings('draft-owner');
    clearNewConversationSettings('another-user');
    list.mockReset().mockResolvedValue({ conversations: [recent] });
  });
  afterEach(() => {
    clearNewConversationSettings('draft-owner');
    clearNewConversationSettings('another-user');
  });

  it('keeps the new conversation on refresh when its owner has saved draft settings', async () => {
    saveConversationSettings('draft-owner', null, { image_aspect_ratio: '1:1', image_taobao_main_image: true });
    const select = vi.fn();
    render(<ConversationList currentConversationId={null} onSelectConversation={select} />);
    await screen.findByText('最近对话');
    expect(select).not.toHaveBeenCalled();
  });

  it('preserves the original recent-conversation selection without a draft', async () => {
    const select = vi.fn();
    render(<ConversationList currentConversationId={null} onSelectConversation={select} />);
    await waitFor(() => expect(select).toHaveBeenCalledWith('recent', '最近对话', null));
  });

  it('ignores another user’s draft when choosing the recent conversation', async () => {
    saveConversationSettings('another-user', null, { image_aspect_ratio: '1:1', image_taobao_main_image: true });
    const select = vi.fn();
    render(<ConversationList currentConversationId={null} onSelectConversation={select} />);
    await waitFor(() => expect(select).toHaveBeenCalledWith('recent', '最近对话', null));
  });

  it('does not replace an explicitly opened conversation', async () => {
    const select = vi.fn();
    render(<ConversationList currentConversationId="opened" onSelectConversation={select} />);
    await screen.findByText('最近对话');
    expect(select).not.toHaveBeenCalled();
  });
});

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { skillCreationService, type SkillChatProposal } from '../../../../services/skillCreation';
import SkillChatProposalCard from '../SkillChatProposalCard';

vi.mock('../../../../services/skillCreation', () => ({
  skillCreationService: {
    getChatProposal: vi.fn(), confirmChatProposal: vi.fn(), cancelChatProposal: vi.fn(),
    feedbackChatProposal: vi.fn(),
  },
}));

function makeProposal(): SkillChatProposal {
  return {
    id: 'proposal-1', skill_key: 'product-image', content_sha256: 'a'.repeat(64), version: 1,
    status: 'awaiting_confirmation',
    content: { description: '生成商品图', body: '保持产品结构。', catalog_metadata: { name: '商品图 Skill' } },
    available_targets: { personal: true, org: false, platform: false },
  };
}

describe('SkillChatProposalCard', () => {
  beforeEach(() => {
    vi.mocked(skillCreationService.getChatProposal).mockResolvedValue(makeProposal());
  });

  afterEach(() => vi.clearAllMocks());

  it('keeps a storage publication error visible after refreshing the proposal', async () => {
    vi.mocked(skillCreationService.confirmChatProposal).mockRejectedValue({
      response: { data: { detail: 'SKILL_STORAGE_WRITE_REJECTED' } },
    });
    render(<SkillChatProposalCard proposalId="proposal-1" />);

    fireEvent.click(await screen.findByRole('button', { name: '确认并继续' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Skill 存储当前不可写，候选已保留。请联系管理员处理后重试。',
    );
    await waitFor(() => expect(skillCreationService.getChatProposal).toHaveBeenCalledTimes(2));
    expect(screen.getByText('核对完整内容，选择 Skill 的保存范围。确认前不会保存或发布。')).toBeInTheDocument();
  });
});

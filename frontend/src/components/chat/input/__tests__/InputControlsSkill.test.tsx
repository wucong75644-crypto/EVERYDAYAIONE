import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { LazyMotion, domAnimation } from 'framer-motion';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SMART_MODEL } from '../../../../constants/smartModel';
import { getAvailableSkills, type SkillSummary } from '../../../../services/skills';
import InputControls from '../InputControls';
import type { InputControlsProps } from '../InputControls.types';
import { useTurnSkillSelection } from '../useTurnSkillSelection';

vi.mock('../../../../services/skills', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../../services/skills')>(),
  getAvailableSkills: vi.fn(),
}));

const skill: SkillSummary = {
  skill_id: 'reference-image-prompts', name: '参考图多方案提示词', revision: 'v1',
  description: '参考图片生成多方案提示词', triggers: [], source: 'platform', model_selectable: false,
};
const alternative = { ...skill, skill_id: 'orders', name: '订单摘要', revision: 'v2' };
const onSend = vi.fn();
const onMentionInputChange = vi.fn();

const baseProps: InputControlsProps = {
  prompt: '', onPromptChange: vi.fn(), onSubmit: vi.fn(), onKeyDown: vi.fn(),
  isSubmitting: false, sendButtonDisabled: false, sendButtonTooltip: '发送消息',
  recordingState: 'idle', audioBlob: null, audioDuration: 0,
  onStartRecording: vi.fn(async () => undefined), onStopRecording: vi.fn(), onClearRecording: vi.fn(),
  selectedModel: SMART_MODEL, availableModels: [SMART_MODEL],
  modelSelectorLocked: false, modelSelectorLockTooltip: '', onSelectModel: vi.fn(),
  estimatedCredits: '自动', creditsHighlight: false,
  aspectRatio: '1:1', onAspectRatioChange: vi.fn(), resolution: '1K', onResolutionChange: vi.fn(),
  outputFormat: 'png', onOutputFormatChange: vi.fn(), numImages: 1, onNumImagesChange: vi.fn(),
  videoFrames: '10', onVideoFramesChange: vi.fn(), videoAspectRatio: 'landscape', onVideoAspectRatioChange: vi.fn(),
  removeWatermark: false, onRemoveWatermarkChange: vi.fn(),
  onSaveSettings: vi.fn(), onResetSettings: vi.fn(), attachments: [], onRemoveAttachment: vi.fn(),
  onMentionInputChange,
};

function Composer({ enabled = true, conversationId = 'conv-1', submitting = false }) {
  const [prompt, setPrompt] = useState('保留我写的需求');
  const selection = useTurnSkillSelection(conversationId, enabled);
  return <LazyMotion features={domAnimation}><InputControls {...baseProps}
    prompt={prompt} onPromptChange={setPrompt} isSubmitting={submitting}
    onSubmit={() => { onSend(prompt, selection.take()); setPrompt(''); }}
    skillSelector={enabled ? {
      conversationId, ensureConversation: async () => conversationId,
      selected: selection.selected, onSelect: selection.select, disabled: submitting,
    } : undefined}
  /></LazyMotion>;
}

async function choose(name = '参考图多方案提示词 · v1') {
  fireEvent.click(screen.getByRole('button', { name: /^(选择 Skill|Skill：)/ }));
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(name) }));
  await waitFor(() => expect(screen.getByRole('textbox')).toHaveFocus());
}

describe('Skill tag in the composer', () => {
  beforeEach(() => vi.mocked(getAvailableSkills).mockResolvedValue([skill, alternative]));

  it('places the selected name before the input and removes only the selection with ×', async () => {
    render(<Composer />);
    await choose();
    const input = screen.getByRole('textbox');
    const tag = screen.getByRole('group', { name: '已选择的 Skill' });
    expect(within(tag).getByText(skill.name)).toBeVisible();
    expect(tag.nextElementSibling).toBe(input);
    expect(input).toHaveValue('保留我写的需求');
    expect(input).toHaveAttribute('placeholder', '描述你的需求…');
    expect(screen.getByRole('button', { name: `Skill：${skill.name}` })).toHaveTextContent(/^Skill$/);

    fireEvent.click(screen.getByRole('button', { name: `取消 Skill：${skill.name}` }));
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    expect(input).toHaveValue('保留我写的需求');
    expect(input).toHaveFocus();
    fireEvent.click(screen.getByTitle('发送消息'));
    expect(onSend).toHaveBeenCalledWith('保留我写的需求', undefined);
  });

  it('replaces a Skill from the toolbar and sends it once without prefixing the message text', async () => {
    render(<Composer />);
    await choose();
    await choose('订单摘要 · v2');
    const tag = screen.getByRole('group', { name: '已选择的 Skill' });
    expect(within(tag).getByText(alternative.name)).toBeVisible();
    expect(within(tag).queryByText(skill.name)).not.toBeInTheDocument();
    fireEvent.click(screen.getByTitle('发送消息'));
    expect(onSend).toHaveBeenLastCalledWith('保留我写的需求', { skill_id: 'orders', revision: 'v2' });
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '普通聊天' } });
    expect(onMentionInputChange).toHaveBeenLastCalledWith('普通聊天', 4);
    fireEvent.click(screen.getByTitle('发送消息'));
    expect(onSend).toHaveBeenLastCalledWith('普通聊天', undefined);
  });

  it('clears the tag on conversation changes and when Skill UI is disabled, preserving text', async () => {
    const view = render(<Composer />);
    await choose();
    view.rerender(<Composer conversationId="conv-2" />);
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    await choose();
    view.rerender(<Composer conversationId="conv-2" enabled={false} />);
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Skill/ })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('保留我写的需求');
  });

  it('disables removal during submission and supports clearing through the selector', async () => {
    const view = render(<Composer />);
    await choose();
    view.rerender(<Composer submitting />);
    const cancel = screen.getByRole('button', { name: `取消 Skill：${skill.name}` });
    expect(cancel).toBeDisabled();
    fireEvent.click(cancel);
    expect(screen.getByRole('group', { name: '已选择的 Skill' })).toBeInTheDocument();
    view.rerender(<Composer />);
    await choose('不手动选择');
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('保留我写的需求');
  });
});

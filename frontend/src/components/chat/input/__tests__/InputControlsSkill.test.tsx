import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { LazyMotion, domAnimation } from 'framer-motion';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SMART_MODEL } from '../../../../constants/smartModel';
import { getAvailableSkills, getSkillBindings, addSkillBinding, type SkillSummary } from '../../../../services/skills';
import InputControls from '../InputControls';
import type { InputControlsProps } from '../InputControls.types';
import { useTurnSkillSelection } from '../useTurnSkillSelection';

vi.mock('../../../../services/skills', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../../services/skills')>(),
  getAvailableSkills: vi.fn(), getSkillBindings: vi.fn(), addSkillBinding: vi.fn(),
}));

const previews = vi.hoisted(() => ({ open: vi.fn(), close: vi.fn(), setIndex: vi.fn() }));
vi.mock('../../../../preview/PreviewHost', () => ({ default: () => null }));
vi.mock('../../../../preview/usePreview', () => ({ usePreview: () => ({ state: { kind: 'closed' }, ...previews }) }));

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

function Composer({ enabled = true, conversationId = 'conv-1', submitting = false, initialAttachments = [] }: { enabled?: boolean; conversationId?: string; submitting?: boolean; initialAttachments?: InputControlsProps['attachments'] }) {
  const [prompt, setPrompt] = useState('保留我写的需求');
  const [attachments, setAttachments] = useState(initialAttachments);
  const selection = useTurnSkillSelection(conversationId, enabled);
  return <LazyMotion features={domAnimation}><InputControls {...baseProps}
    prompt={prompt} onPromptChange={setPrompt} isSubmitting={submitting}
    attachments={attachments} onRemoveAttachment={id => setAttachments(items => items.filter(item => item.id !== id))}
    onSubmit={() => { onSend(prompt, selection.take()); setPrompt(''); }}
    skillSelector={enabled ? {
      conversationId, ensureConversation: async () => conversationId,
      selected: selection.selected, onSelect: selection.select, disabled: submitting,
    } : undefined}
  /></LazyMotion>;
}

async function choose(name = '选择 Skill：参考图多方案提示词') {
  fireEvent.click(screen.getByRole('button', { name: /^(选择 Skill|Skill：)/ }));
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(name) }));
  await waitFor(() => expect(screen.getByRole('textbox')).toHaveFocus());
}

describe('Skill tag in the composer', () => {
  beforeEach(() => { vi.mocked(getAvailableSkills).mockResolvedValue([skill, alternative]); vi.mocked(getSkillBindings).mockResolvedValue([]); vi.mocked(addSkillBinding).mockResolvedValue({ binding_id: 'fixed-1' }); });

  it('places the selected name before the input and removes only the selection with ×', async () => {
    render(<Composer />);
    await choose();
    const input = screen.getByRole('textbox');
    const tag = screen.getByRole('group', { name: '已选择的 Skill' });
    expect(within(tag).getByText(skill.name)).toBeVisible();
    const scope = screen.getByRole('button', { name: /使用范围：参考图/ });
    expect(tag).toContainElement(scope);
    expect(scope).not.toHaveTextContent('仅本条');
    expect(scope).toHaveAttribute('title', '仅本条');
    expect(input.parentElement).toContainElement(tag);
    expect(tag.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tag).toHaveClass('border', 'bg-hover', 'text-text-secondary');
    expect(tag).not.toHaveClass('bg-accent-light');
    expect(input).toHaveValue('保留我写的需求');
    expect(input).toHaveAttribute('placeholder', '发送消息...');
    expect(screen.getByRole('button', { name: `Skill：${skill.name}` })).toHaveTextContent(/^Skill$/);

    fireEvent.click(screen.getByRole('button', { name: `取消 Skill：${skill.name}` }));
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    expect(input).toHaveValue('保留我写的需求');
    expect(input).toHaveFocus();
    fireEvent.click(screen.getByTitle('发送消息'));
    expect(onSend).toHaveBeenCalledWith('保留我写的需求', undefined);
  });

  it('preserves multiline text, caret offsets and keyboard handling with an inline tag', async () => {
    render(<Composer />);
    await choose();
    const input = screen.getByRole('textbox');
    const prompt = '保持细节清晰\n不要添加新的商品 @资料';
    fireEvent.change(input, { target: { value: prompt, selectionStart: 18 } });
    expect(input).toHaveValue(prompt);
    expect(onMentionInputChange).toHaveBeenLastCalledWith(prompt, 18);
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(baseProps.onKeyDown).toHaveBeenCalled();
    fireEvent.click(screen.getByTitle('发送消息'));
    expect(onSend).toHaveBeenLastCalledWith(prompt, { skill_id: skill.skill_id, revision: skill.revision });
    expect(input).toHaveStyle({ textIndent: '' });
  });

  it('replaces a Skill from the toolbar and sends it once without prefixing the message text', async () => {
    render(<Composer />);
    await choose();
    await choose('选择 Skill：订单摘要');
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
    await act(async () => view.rerender(<Composer conversationId="conv-2" />));
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    await choose();
    await act(async () => view.rerender(<Composer conversationId="conv-2" enabled={false} />));
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Skill/ })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('保留我写的需求');
  });

  it('disables removal and scope changes during submission', async () => {
    const view = render(<Composer />);
    await choose();
    view.rerender(<Composer submitting />);
    const cancel = screen.getByRole('button', { name: `取消 Skill：${skill.name}` });
    expect(cancel).toBeDisabled();
    fireEvent.click(cancel);
    expect(screen.getByRole('group', { name: '已选择的 Skill' })).toBeInTheDocument();
    view.rerender(<Composer />);
    fireEvent.click(screen.getByRole('button', { name: `取消 Skill：${skill.name}` }));
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('保留我写的需求');
  });
  it('keeps mixed attachments above tags and preserves preview, delete and text independently', async () => {
    const attachments: InputControlsProps['attachments'] = [
      { id: 'img', sourceId: 'img', kind: 'image', source: 'upload', status: 'ready', name: '参考图.png', previewUrl: '/thumb.png', thumbnailUrl: '/thumb.png', originalUrl: '/original.png' },
      { id: 'pdf', sourceId: 'pdf', kind: 'file', source: 'upload', status: 'ready', name: '说明.pdf', mimeType: 'application/pdf', size: 2048, url: '/report.pdf' },
      { id: 'sheet', sourceId: 'sheet', kind: 'file', source: 'workspace', status: 'ready', name: '资料.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', size: 4096, url: '/data.xlsx' },
    ];
    render(<Composer initialAttachments={attachments} />);
    const input = screen.getByRole('textbox');
    const area = input.parentElement!.previousElementSibling!;
    const before = area.innerHTML;
    const image = screen.getByRole('img', { name: '参考图.png' });
    await choose();
    expect(area.nextElementSibling).toBe(input.parentElement);
    expect(input.parentElement).toContainElement(screen.getByRole('group', { name: '已选择的 Skill' }));
    expect(area.innerHTML).toBe(before);
    fireEvent.click(image);
    expect(previews.open).toHaveBeenCalled();
    expect(image).toHaveAttribute('src', '/thumb.png');
    fireEvent.click(screen.getByRole('button', { name: `取消 Skill：${skill.name}` }));
    expect(area.innerHTML).toBe(before);
    await choose();
    fireEvent.click(screen.getByRole('button', { name: /使用范围：参考图/ }));
    fireEvent.click(screen.getByRole('button', { name: /固定到当前会话/ }));
    await screen.findByRole('group', { name: '会话固定的 Skill' });
    expect(area.innerHTML).toBe(before);
    fireEvent.click(screen.getByRole('button', { name: '移除 说明.pdf' }));
    expect(screen.queryByText('说明.pdf')).not.toBeInTheDocument();
    expect(image).toBeInTheDocument();
    expect(screen.getByText('资料.xlsx')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '会话固定的 Skill' })).toBeInTheDocument();
    expect(input).toHaveValue('保留我写的需求');
  });

  it('holds click and Enter submission until a scope mutation is acknowledged', async () => {
    let resolve!: (result: { binding_id: string }) => void;
    vi.mocked(addSkillBinding).mockReturnValueOnce(new Promise(r => { resolve = r; }));
    render(<Composer />);
    await choose();
    fireEvent.click(screen.getByRole('button', { name: /使用范围：参考图/ }));
    fireEvent.click(screen.getByRole('button', { name: /固定到当前会话/ }));
    expect(screen.getByTitle('发送消息')).toBeDisabled();
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(baseProps.onKeyDown).not.toHaveBeenCalled();
    await act(async () => resolve({ binding_id: 'fixed-1' }));
    expect(screen.getByTitle('发送消息')).toBeEnabled();
    fireEvent.click(screen.getByTitle('发送消息'));
    expect(onSend).toHaveBeenLastCalledWith('保留我写的需求', undefined);
    expect(screen.getByRole('group', { name: '会话固定的 Skill' })).toBeInTheDocument();
  });

});

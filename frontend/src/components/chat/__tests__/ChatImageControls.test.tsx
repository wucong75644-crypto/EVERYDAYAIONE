import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChatImageControls from '../media/ChatImageControls';
import { downloadWorkspaceZip } from '../../../services/workspace';
import { chatImageService } from '../../../services/chatImage';

vi.mock('../../../services/chatImage', () => ({ chatImageService: { details: vi.fn(), stop: vi.fn(), feedback: vi.fn(), estimate: vi.fn() } }));

vi.mock('../../../services/workspace', () => ({ downloadWorkspaceZip: vi.fn() }));

describe('ChatImageControls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(chatImageService.details).mockResolvedValue({ task_id: 'task', message_id: 'message', status: 'running',
      submission_state: 'accepted', credits_used: 0, can_stop: false, can_replay: false,
      cancel_explanation: '任务已提交，无法撤回，将继续核实与结算',
      input: { schema_version: 1, prompt: '  exact server prompt\n', prompt_sha256: 'hash', request_hash: 'hash',
        mode: 'image_to_image', model: 'actual-model', aspect_ratio: '1:1', resolution: '1K', output_format: 'png',
        estimated_credits: 7, estimated_provider_credits: 6, references: [{ role: '产品结构', workspace_path: '原图/B.png', content_sha256: 'hash', size: 123 }],
        origin: { parent_task_id: 'parent' }, budget: { max_requests: 4, max_credits: 100 } } });
  });

  it('downloads both saved versions using the existing workspace ZIP endpoint', async () => {
    const base=await chatImageService.details('task');
    vi.mocked(chatImageService.details).mockResolvedValueOnce({ ...base, task_id: 'new', submission_state: 'published',
      input: { ...base.input, origin: { parent_task_id: 'parent', retry_of_task_id: 'old' } },
      result: [{ type: 'image', url: 'new.png', workspace_path: 'generated/new.png' }] });
    vi.mocked(chatImageService.details).mockResolvedValueOnce({ ...base, task_id: 'old', submission_state: 'published',
      result: [{ type: 'image', url: 'old.png', workspace_path: 'generated/old.png' }] });
    const view=render(<ChatImageControls taskId="new" />);
    fireEvent.click(screen.getByRole('button', { name: '图片任务详情' }));
    fireEvent.click(await screen.findByRole('button', { name: '比较旧版本' }));
    fireEvent.click(await screen.findByRole('button', { name: '下载对比图 ZIP' }));
    await waitFor(() => expect(downloadWorkspaceZip).toHaveBeenCalledWith(['generated/old.png', 'generated/new.png']));
    view.unmount();
  });

  it('previews current server cost without submitting a generation', async () => {
    vi.mocked(chatImageService.estimate).mockResolvedValue({ per_image_credits: 16, total_credits: 16, image_count: 1,
      acceptance_enabled: false, within_budget: true, max_requests: 4, max_credits: 100 });
    const view=render(<ChatImageControls taskId="task" />);
    fireEvent.click(screen.getByRole('button', { name: '图片任务详情' }));
    fireEvent.click(await screen.findByRole('button', { name: '预览再生成成本' }));
    await screen.findByText(/服务器当前估算：每张 16 积分/);
    expect(chatImageService.estimate).toHaveBeenCalledWith(expect.objectContaining({ prompt: '  exact server prompt\n' }));
    view.unmount();
  });

  it('reads exact server input and explains why an accepted task cannot be stopped', async () => {
    const view=render(<ChatImageControls taskId="task" />);
    fireEvent.click(screen.getByRole('button', { name: '图片任务详情' }));
    await waitFor(() => expect(chatImageService.details).toHaveBeenCalledWith('task'));
    await screen.findByText('服务器实际执行提示词');
    expect(view.container.querySelector('pre')?.textContent).toBe('  exact server prompt\n');
    expect(screen.getByText(/产品结构 · 原图\/B.png/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '停止排队' })).not.toBeInTheDocument();
    expect(screen.getByText(/任务已提交，无法撤回/)).toBeInTheDocument();
    view.unmount();
  });
});

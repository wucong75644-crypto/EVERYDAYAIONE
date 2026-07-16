import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useInputDraftTransaction } from '../useInputDraftTransaction';

describe('useInputDraftTransaction', () => {
  it('clears immediately and restores a rejected draft before newer input', () => {
    const setPrompt = vi.fn();
    const { result, rerender } = renderHook(
      ({ prompt }) => useInputDraftTransaction({
        prompt, setPrompt, workspaceFiles: [],
      }),
      { initialProps: { prompt: '旧草稿' } },
    );

    act(() => result.current.clearPromptForSubmission());
    expect(setPrompt).toHaveBeenLastCalledWith('');

    rerender({ prompt: '等待期间的新草稿' });
    act(() => result.current.restorePromptAfterRejection('旧草稿'));
    expect(setPrompt).toHaveBeenLastCalledWith('旧草稿\n等待期间的新草稿');
  });

  it('detaches workspace files and restores through the deduplicating owner callback', () => {
    const file = {
      name: 'brief.pdf', workspace_path: 'docs/brief.pdf', cdn_url: 'https://cdn/brief.pdf',
      mime_type: 'application/pdf', size: 10,
    };
    const consumeWorkspaceFiles = vi.fn();
    const addWorkspaceFile = vi.fn();
    const { result } = renderHook(() => useInputDraftTransaction({
      prompt: '', setPrompt: vi.fn(), workspaceFiles: [file],
      consumeWorkspaceFiles, addWorkspaceFile,
    }));

    let restore = () => undefined;
    act(() => { restore = result.current.detachWorkspaceFilesForSubmission(); });
    expect(consumeWorkspaceFiles).toHaveBeenCalledOnce();
    act(() => restore());
    expect(addWorkspaceFile).toHaveBeenCalledWith(file);
  });
});

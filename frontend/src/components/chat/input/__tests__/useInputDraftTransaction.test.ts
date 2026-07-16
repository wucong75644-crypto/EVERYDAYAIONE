import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useInputDraftTransaction } from '../useInputDraftTransaction';

describe('useInputDraftTransaction', () => {
  it('clears immediately and restores a rejected draft before newer input', () => {
    const setPrompt = vi.fn();
    const { result, rerender } = renderHook(
      ({ prompt }) => useInputDraftTransaction({
        prompt, setPrompt,
      }),
      { initialProps: { prompt: '旧草稿' } },
    );

    act(() => result.current.clearPromptForSubmission());
    expect(setPrompt).toHaveBeenLastCalledWith('');

    rerender({ prompt: '等待期间的新草稿' });
    act(() => result.current.restorePromptAfterRejection('旧草稿'));
    expect(setPrompt).toHaveBeenLastCalledWith('旧草稿\n等待期间的新草稿');
  });
});

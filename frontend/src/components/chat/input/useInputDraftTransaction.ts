import { useCallback, useEffect, useRef } from 'react';

interface DraftTransactionOptions {
  prompt: string;
  setPrompt: (value: string) => void;
}

/** 管理编辑器草稿的立即移出和明确拒绝恢复，不覆盖等待期间的新草稿。 */
export function useInputDraftTransaction(options: DraftTransactionOptions) {
  const { prompt, setPrompt } = options;
  const promptRef = useRef(prompt);

  useEffect(() => {
    promptRef.current = prompt;
  }, [prompt]);

  useEffect(() => {
    const handleQuoteText = (event: Event) => {
      const { text } = (event as CustomEvent<{ text: string }>).detail;
      if (!text?.trim()) return;
      const current = promptRef.current;
      const next = current ? `${text}\n${current}` : text;
      promptRef.current = next;
      setPrompt(next);
    };
    window.addEventListener('chat:quote-text', handleQuoteText);
    return () => window.removeEventListener('chat:quote-text', handleQuoteText);
  }, [setPrompt]);

  const clearPromptForSubmission = useCallback(() => {
    promptRef.current = '';
    setPrompt('');
  }, [setPrompt]);

  const restorePromptAfterRejection = useCallback((submittedPrompt: string) => {
    const current = promptRef.current;
    const restored = current.trim() ? `${submittedPrompt}\n${current}` : submittedPrompt;
    promptRef.current = restored;
    setPrompt(restored);
  }, [setPrompt]);

  return {
    clearPromptForSubmission,
    restorePromptAfterRejection,
  };
}

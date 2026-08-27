const RELOAD_GUARD_KEY = 'everydayai_chunk_reload_attempted';
const RECOVERY_WINDOW_MS = 10_000;

/**
 * Recover once when an open page references chunks removed by a newer deploy.
 * A second failure is allowed to reach the ErrorBoundary instead of looping.
 */
export function installChunkLoadRecovery(
  reload: () => void = () => window.location.reload(),
): () => void {
  const handlePreloadError = (event: Event): void => {
    if (sessionStorage.getItem(RELOAD_GUARD_KEY)) return;

    event.preventDefault();
    sessionStorage.setItem(RELOAD_GUARD_KEY, '1');
    reload();
  };

  window.addEventListener('vite:preloadError', handlePreloadError);
  const resetTimer = window.setTimeout(() => {
    sessionStorage.removeItem(RELOAD_GUARD_KEY);
  }, RECOVERY_WINDOW_MS);

  return () => {
    window.removeEventListener('vite:preloadError', handlePreloadError);
    window.clearTimeout(resetTimer);
  };
}

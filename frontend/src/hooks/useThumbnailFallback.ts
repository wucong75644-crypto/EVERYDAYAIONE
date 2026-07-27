import { useCallback, useState } from 'react';
import { toDisplayThumbnailUrl, toOriginalImageUrl } from '../utils/imageUrlRules';

type ThumbnailLoadState = 'thumbnail' | 'original' | 'failed';
type LoadState = { key: string; value: ThumbnailLoadState };

export function useThumbnailFallback(
  thumbnailUrl: string | null | undefined,
  originalUrl: string | null | undefined,
) {
  const thumbnail = toDisplayThumbnailUrl(thumbnailUrl);
  const original = toOriginalImageUrl(originalUrl);
  const initialState: ThumbnailLoadState = thumbnail
    ? 'thumbnail'
    : original
      ? 'original'
      : 'failed';
  const key = `${thumbnail}\0${original}`;
  const [loadState, setLoadState] = useState<LoadState>({ key, value: initialState });
  const state = loadState.key === key ? loadState.value : initialState;

  const onError = useCallback(() => {
    setLoadState({
      key,
      value: state === 'thumbnail' && original && original !== thumbnail
        ? 'original'
        : 'failed',
    });
  }, [key, original, state, thumbnail]);
  const reset = useCallback(() => {
    setLoadState({ key, value: initialState });
  }, [initialState, key]);

  return {
    src: state === 'thumbnail' ? thumbnail : state === 'original' ? original : '',
    failed: state === 'failed',
    onError,
    reset,
  };
}

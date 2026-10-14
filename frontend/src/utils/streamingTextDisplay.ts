import type { ContentPart } from '../types/message';

// Display metadata only; it never becomes part of a message or WS payload.
// Copying the boundary also covers a restore and live delta batched by React.
interface Snapshot {
  content: readonly ContentPart[];
}

export interface StreamingTextDisplayBaseline {
  snapshot: object;
  text: string;
}

const snapshots = new WeakMap<readonly ContentPart[], Snapshot>();

export function markStreamingTextDisplaySnapshot<T extends ContentPart[]>(content: T): T {
  snapshots.set(content, { content });
  return content;
}

export function inheritStreamingTextDisplaySnapshot<T extends ContentPart[]>(content: T, previous: readonly ContentPart[]): T {
  const snapshot = snapshots.get(previous);
  if (snapshot) snapshots.set(content, snapshot);
  return content;
}

export function streamingTextDisplayBaseline(content: readonly ContentPart[], index?: number): StreamingTextDisplayBaseline | undefined {
  const snapshot = snapshots.get(content);
  if (!snapshot) return undefined;
  const parts = index === undefined ? snapshot.content : [snapshot.content[index]];
  return {
    snapshot,
    text: parts.filter((part) => part?.type === 'text').map((part) => (part as { text: string }).text).join('\n\n'),
  };
}

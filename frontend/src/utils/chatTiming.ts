/** Passive, bounded per-attempt timings. No content, credentials or network IO. */
import { logger } from './logger';

type Row = {
  messageId: string; taskId?: string; backendTaskId?: string; streamId?: string; attempt?: number;
  started?: number; accepted?: number; firstChunk?: number; firstApplied?: number;
  firstDomFrame?: number; terminal?: number; lastChunk?: number; chunks: number; maxGap: number;
};
const rows = new Map<string, Row>();
const now = () => performance.now();
const log = (message: string, data: ReturnType<typeof readChatTiming>) => {
  try { logger.info('chat:timing', message, data); } catch { /* Diagnostics never gate UI. */ }
};
const bounded = (row: Row) => {
  rows.delete(row.messageId); rows.set(row.messageId, row);
  if (rows.size > 100) rows.delete(rows.keys().next().value!);
  return row;
};
export function startChatTiming(messageId: string, taskId: string) {
  bounded({ messageId, taskId, started: now(), chunks: 0, maxGap: 0 });
}
export function acceptChatTiming(messageId: string, backendTaskId?: string, actualMessageId?: string, expectedTaskId?: string) {
  const row = rows.get(messageId);
  if (!row || (expectedTaskId && row.taskId !== expectedTaskId)) return;
  row.accepted = now(); row.backendTaskId = backendTaskId;
  if (actualMessageId && actualMessageId !== messageId) {
    if (rows.has(actualMessageId)) return; // Preserve an earlier WS/recovery observation.
    rows.delete(messageId); row.messageId = actualMessageId; bounded(row);
  }
}
export function receiveChatChunk(messageId: string, taskId?: string, streamId?: string, attempt?: number) {
  let row = rows.get(messageId);
  if (!row || (streamId && row.streamId && row.streamId !== streamId)
      || (attempt !== undefined && row.attempt !== undefined && attempt !== row.attempt)) {
    // Recovery is measured from its first event, never from an old send/pause.
    row = bounded({ messageId, taskId, chunks: 0, maxGap: 0 });
  }
  const at = now();
  row.taskId ??= taskId; row.streamId ??= streamId; row.attempt ??= attempt;
  row.firstChunk ??= at;
  if (row.lastChunk !== undefined) row.maxGap = Math.max(row.maxGap, at - row.lastChunk);
  row.lastChunk = at; row.chunks += 1;
}
export function appliedChatChunk(messageId: string) {
  const row = rows.get(messageId);
  if (row) row.firstApplied ??= now();
}
export function finishChatTiming(messageId: string, taskId?: string, streamId?: string, attempt?: number) {
  const row = rows.get(messageId);
  if (!row || row.terminal !== undefined || (row.streamId !== undefined && streamId !== row.streamId)
      || (row.attempt !== undefined && attempt !== row.attempt)
      || (taskId && row.taskId && taskId !== row.taskId && taskId !== row.backendTaskId)) return;
  row.terminal = now();
  log('terminal received', readChatTiming(messageId));
}
export function readChatTiming(messageId: string) {
  const row = rows.get(messageId);
  if (!row) return undefined;
  const delta = (value?: number) => value === undefined || row.started === undefined ? null : Math.round((value - row.started) * 1000) / 1000;
  return { messageId: row.messageId, taskId: row.taskId, backendTaskId: row.backendTaskId,
    streamId: row.streamId, attempt: row.attempt, timeOrigin: performance.timeOrigin,
    acceptedMs: delta(row.accepted), firstChunkMs: delta(row.firstChunk),
    firstAppliedMs: delta(row.firstApplied), firstDomFrameMs: delta(row.firstDomFrame), terminalMs: delta(row.terminal),
    eventToDomFrameMs: row.firstDomFrame === undefined || row.firstChunk === undefined ? null : row.firstDomFrame - row.firstChunk,
    chunks: row.chunks, maxChunkGapMs: row.maxGap,
    firstDomBeforeTerminal: row.firstDomFrame === undefined ? null : row.terminal === undefined || row.firstDomFrame < row.terminal };
}
export function observeChatBody(messageId: string, element: HTMLElement) {
  const row = rows.get(messageId);
  if (!row || row.started === undefined || row.firstChunk === undefined || row.firstDomFrame !== undefined) return () => {};
  let frame: number | undefined;
  const check = () => {
    if (!element.querySelector('[data-chat-body]')?.textContent?.trim() || frame !== undefined) return;
    frame = requestAnimationFrame(() => {
      observer.disconnect();
      if (rows.get(messageId) !== row || !element.isConnected || document.visibilityState !== 'visible') return;
      row.firstDomFrame ??= now();
      log('body DOM frame', readChatTiming(messageId));
    });
  };
  const observer = new MutationObserver(check);
  observer.observe(element, { childList: true, characterData: true, subtree: true }); check();
  return () => { observer.disconnect(); if (frame !== undefined) cancelAnimationFrame(frame); };
}

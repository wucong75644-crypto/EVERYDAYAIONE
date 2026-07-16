/** Handle routing completion without coupling media placeholder construction to the handler registry. */

import { getPlaceholderText } from '../constants/placeholder';
import type { HandlerDeps, WSIncomingMessage } from './wsMessageHandlerShared';

const MEDIA_GENERATION_TYPES = new Set(['image', 'image_ecom', 'video', 'audio']);

function getLoadingText(
  generationType: string,
  generationParams: Record<string, unknown> | undefined,
): string {
  const render = generationParams?._render as Record<string, unknown> | undefined;
  if (typeof render?.placeholder_text === 'string') return render.placeholder_text;
  const placeholderType = generationType === 'image_ecom' ? 'image' : generationType;
  return getPlaceholderText(placeholderType as 'image' | 'video' | 'audio');
}

export function handleRoutingComplete(deps: HandlerDeps, msg: WSIncomingMessage): void {
  const { conversation_id, message_id } = msg;
  const generationType = typeof msg.payload?.generation_type === 'string'
    ? msg.payload.generation_type
    : undefined;
  const model = typeof msg.payload?.model === 'string' ? msg.payload.model : undefined;
  const generationParams = msg.payload?.generation_params;
  const params = generationParams && typeof generationParams === 'object' && !Array.isArray(generationParams)
    ? generationParams as Record<string, unknown>
    : undefined;
  if (!conversation_id || !generationType || !message_id) return;

  const store = deps.getStore();
  if (!MEDIA_GENERATION_TYPES.has(generationType)) {
    store.updateMessage(message_id, { generation_params: params ?? { model } });
    return;
  }

  store.completeStreamingWithMessage(conversation_id, {
    id: message_id,
    conversation_id,
    role: 'assistant',
    content: [{ type: 'text', text: getLoadingText(generationType, params) }],
    status: 'pending',
    created_at: new Date().toISOString(),
    generation_params: params ?? { model },
    task_id: msg.task_id,
  });
  store.setIsSending(true);
}

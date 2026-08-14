import type {
  ContentPart,
  ImagePart,
  RuntimeMediaSlotStatus,
} from '../types/message';

export const RUNTIME_MEDIA_SLOT_LIMIT = 10;

const SLOT_STATUSES = new Set<RuntimeMediaSlotStatus>([
  'pending',
  'accepted',
  'unknown',
  'completed',
  'failed',
  'cancelled',
]);

export type RuntimeMediaImageSlot = ImagePart & Required<Pick<
  ImagePart,
  'slot_id' | 'slot_index' | 'slot_status' | 'slot_revision'
>>;

export interface RuntimeMediaSlotSummary {
  total: number;
  pending: number;
  accepted: number;
  unknown: number;
  completed: number;
  failed: number;
  cancelled: number;
  active: number;
}

export function isRuntimeMediaImageSlot(part: ContentPart): part is RuntimeMediaImageSlot {
  if (part.type !== 'image') return false;
  const { slot_id: slotId, slot_index: slotIndex, slot_status: slotStatus } = part;
  const slotRevision = part.slot_revision;
  return typeof slotId === 'string'
    && slotId.length > 0
    && slotIndex !== undefined
    && Number.isInteger(slotIndex)
    && slotIndex >= 0
    && slotIndex < RUNTIME_MEDIA_SLOT_LIMIT
    && SLOT_STATUSES.has(slotStatus as RuntimeMediaSlotStatus)
    && slotRevision !== undefined
    && Number.isInteger(slotRevision)
    && slotRevision >= 0;
}

export function getRuntimeMediaImageSlots(content: ContentPart[]): RuntimeMediaImageSlot[] {
  const slotsByIndex = new Map<number, RuntimeMediaImageSlot>();
  content.forEach((part) => {
    if (!isRuntimeMediaImageSlot(part)) return;
    const current = slotsByIndex.get(part.slot_index);
    if (!current || part.slot_revision > current.slot_revision) {
      slotsByIndex.set(part.slot_index, part);
    }
  });
  return [...slotsByIndex.values()].sort((left, right) => left.slot_index - right.slot_index);
}

export function isRuntimeMediaSlotActive(status: RuntimeMediaSlotStatus): boolean {
  return status === 'pending' || status === 'accepted' || status === 'unknown';
}

export function summarizeRuntimeMediaSlots(
  slots: RuntimeMediaImageSlot[],
): RuntimeMediaSlotSummary {
  const summary: RuntimeMediaSlotSummary = {
    total: slots.length,
    pending: 0,
    accepted: 0,
    unknown: 0,
    completed: 0,
    failed: 0,
    cancelled: 0,
    active: 0,
  };
  slots.forEach((slot) => {
    summary[slot.slot_status] += 1;
    if (isRuntimeMediaSlotActive(slot.slot_status)) summary.active += 1;
  });
  return summary;
}

export function findRuntimeMediaSlotContentIndex(
  content: ContentPart[],
  slotId: string | undefined,
  slotIndex: number | undefined,
): number {
  if (slotId) {
    const byId = content.findIndex(
      (part) => isRuntimeMediaImageSlot(part) && part.slot_id === slotId,
    );
    if (byId >= 0) return byId;
  }
  if (slotIndex === undefined) return -1;
  return content.findIndex(
    (part) => isRuntimeMediaImageSlot(part) && part.slot_index === slotIndex,
  );
}

export function findImagePartContentIndex(
  content: ContentPart[],
  imageIndex: number,
): number {
  if (!Number.isInteger(imageIndex) || imageIndex < 0) return -1;
  let currentImageIndex = -1;
  return content.findIndex((part) => {
    if (part.type !== 'image') return false;
    currentImageIndex += 1;
    return currentImageIndex === imageIndex;
  });
}

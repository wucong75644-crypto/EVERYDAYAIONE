import type { ChangeSet } from '../types/changeset';

export const CHANGESET_UPDATED_EVENT = 'changeset:updated';
export const CHANGESET_ACTION_EVENT = 'changeset:action';

export type ChangeSetAction =
  | 'confirm'
  | 'cancel'
  | 'retry'
  | 'replan'
  | 'resolve_conflict';

export interface ChangeSetUpdatedDetail {
  changeSetId: string;
  changeSet?: ChangeSet;
  source?: string;
}

export interface ChangeSetActionDetail {
  action: ChangeSetAction;
  changeSetId: string;
  revision: number;
}

export function notifyChangeSetUpdated(
  detail: ChangeSetUpdatedDetail,
): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<ChangeSetUpdatedDetail>(CHANGESET_UPDATED_EVENT, { detail }));
}

export function dispatchChangeSetAction(detail: ChangeSetActionDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<ChangeSetActionDetail>(CHANGESET_ACTION_EVENT, { detail }));
}

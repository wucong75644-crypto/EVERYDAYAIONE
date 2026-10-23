import { useCanvasSurfaceMotion } from './useCanvasSurfaceMotion';
import { useRef } from 'react';
import type { ReactNode } from 'react';
import * as Menu from '@radix-ui/react-dropdown-menu';

type Props = { position: { x: number; y: number }; onClose: () => void; onReturnFocus: () => void; children: ReactNode };
/** A pointer-anchored menu; the canvas drop point remains independent of popup collision handling. */
export function CanvasContextMenu({ position, onClose, onReturnFocus, children }: Props) {
  const restore = useRef(true), content = useRef<HTMLDivElement>(null);
  const attach = useCanvasSurfaceMotion(content, 'menu');
  return <Menu.Root open modal={false} onOpenChange={open => { if (!open) onClose(); }}>
    <Menu.Trigger asChild><span aria-hidden="true" tabIndex={-1} style={{ position: 'fixed', left: position.x, top: position.y, width: 0, height: 0, pointerEvents: 'none' }} /></Menu.Trigger>
    <Menu.Portal><Menu.Content ref={attach} className="kc-menu" aria-label="画布右键菜单" side="right" align="start" sideOffset={0} collisionPadding={8} loop
      onFocus={event => { if (event.target === event.currentTarget) event.currentTarget.querySelector<HTMLElement>('[role="menuitem"]:not([data-disabled])')?.focus({ preventScroll: true }); }}
      onCloseAutoFocus={event => { event.preventDefault(); if (restore.current) onReturnFocus(); }}
      onInteractOutside={event => { restore.current = !(event.target instanceof Element && event.target.closest('button,input,textarea,select,a,[contenteditable],[tabindex]')); }}
      onKeyDown={event => { event.stopPropagation(); if (event.key === 'Tab') { event.preventDefault(); onClose(); } }}>
      {children}
    </Menu.Content></Menu.Portal>
  </Menu.Root>;
}
export function CanvasMenuItem({ disabled, onSelect, children }: { disabled?: boolean; onSelect: () => void; children: ReactNode }) {
  return <Menu.Item asChild disabled={disabled} onSelect={onSelect}><button type="button" disabled={disabled}>{children}</button></Menu.Item>;
}

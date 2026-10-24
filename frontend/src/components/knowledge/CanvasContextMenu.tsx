import { useCanvasSurfaceMotion } from "./useCanvasSurfaceMotion";
import { useRef, useState } from "react";
import type { ReactNode } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";

type Props = {
  position: { x: number; y: number };
  onClose: () => void;
  onReturnFocus: () => void;
  children: ReactNode;
};
/** A pointer-anchored menu; the canvas drop point remains independent of popup collision handling. */
export function CanvasContextMenu({
  position,
  onClose,
  onReturnFocus,
  children,
}: Props) {
  const restore = useRef(true),
    content = useRef<HTMLDivElement>(null);
  const attach = useCanvasSurfaceMotion(content, "menu");
  return (
    <Menu.Root
      open
      modal={false}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Menu.Trigger asChild>
        <span
          aria-hidden="true"
          tabIndex={-1}
          style={{
            position: "fixed",
            left: position.x,
            top: position.y,
            width: 0,
            height: 0,
            pointerEvents: "none",
          }}
        />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          ref={attach}
          className="kc-menu"
          aria-label="画布右键菜单"
          aria-labelledby={undefined}
          side="right"
          align="start"
          sideOffset={0}
          collisionPadding={8}
          loop
          onFocus={(event) => {
            if (event.target === event.currentTarget)
              event.currentTarget
                .querySelector<HTMLElement>(
                  '[role="menuitem"]:not([data-disabled])',
                )
                ?.focus({ preventScroll: true });
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (restore.current) onReturnFocus();
          }}
          onInteractOutside={(event) => {
            restore.current = !(
              event.target instanceof Element &&
              event.target.closest(
                "button,input,textarea,select,a,[contenteditable],[tabindex]",
              )
            );
          }}
          onKeyDown={(event) => {
            event.stopPropagation();
            if (event.key === "Tab") {
              event.preventDefault();
              onClose();
            }
          }}
        >
          {children}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
export function CanvasMenuItem({
  disabled,
  onSelect,
  children,
}: {
  disabled?: boolean;
  onSelect: () => void;
  children: ReactNode;
}) {
  return (
    <Menu.Item asChild disabled={disabled} onSelect={onSelect}>
      <button type="button" disabled={disabled}>
        {children}
      </button>
    </Menu.Item>
  );
}

/** Portal-backed child choices share keyboard handling, collision avoidance and reduced motion. */
export function CanvasSubMenu({
  label,
  disabled,
  children,
}: {
  label: string;
  disabled?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null),
    attach = useCanvasSurfaceMotion(content, "menu");
  return (
    <Menu.Sub open={open} onOpenChange={setOpen}>
      <Menu.SubTrigger asChild disabled={disabled}>
        <button
          ref={trigger}
          type="button"
          className="kc-sub-trigger"
          disabled={disabled}
        >
          {label}
          <span aria-hidden="true">›</span>
        </button>
      </Menu.SubTrigger>
      <Menu.Portal>
        <Menu.SubContent
          ref={attach}
          className="kc-menu kc-submenu"
          aria-label={label + "选项"}
          aria-labelledby={undefined}
          sideOffset={4}
          alignOffset={-4}
          collisionPadding={8}
          loop
          onEscapeKeyDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
            trigger.current?.focus({ preventScroll: true });
          }}
          onKeyDown={(event) => event.stopPropagation()}
        >
          {children}
        </Menu.SubContent>
      </Menu.Portal>
    </Menu.Sub>
  );
}

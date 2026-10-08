import { Check, ChevronDown } from 'lucide-react';
import { DropdownMenu, DropdownMenuItem } from '../primitives/DropdownMenu';
import { cn } from '../../utils/cn';

export interface SelectOption<T extends string> { value: T; label: string }
interface SelectProps<T extends string> {
  value: T;
  options: readonly SelectOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
  ariaLabel: string;
  size?: 'default' | 'compact';
}

export function Select<T extends string>({ value, options, onChange, disabled = false, ariaLabel, size = 'default' }: SelectProps<T>) {
  const selected = options.find((option) => option.value === value);
  return <DropdownMenu
    minWidth="var(--radix-dropdown-menu-trigger-width)"
    trigger={<button type="button" disabled={disabled} aria-label={ariaLabel} className={cn(
      'flex w-full items-center justify-between gap-2 text-sm',
      size === 'compact' ? 'h-8 px-2 py-1' : 'px-3 py-2',
      'rounded-[var(--c-input-radius)] bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
      'border border-[var(--c-input-border)] outline-none',
      'data-[state=open]:border-[var(--c-input-border-focus)] data-[state=open]:shadow-[var(--c-input-ring-focus)]',
      'disabled:opacity-50 disabled:pointer-events-none',
    )}><span className="truncate">{selected?.label ?? value}</span><ChevronDown className="w-4 h-4 shrink-0" /></button>}
  >
    <div className="max-h-60 overflow-y-auto py-1">
      {options.map((option) => <DropdownMenuItem key={option.value} onSelect={() => onChange(option.value)} className="min-h-9">
        <span className="flex w-full items-center justify-between gap-3"><span>{option.label}</span>{option.value === value && <Check className="w-4 h-4 text-[var(--s-accent)]" />}</span>
      </DropdownMenuItem>)}
    </div>
  </DropdownMenu>;
}

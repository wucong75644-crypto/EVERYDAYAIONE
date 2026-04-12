/**
 * 工作区面包屑导航
 */

import { ChevronRight } from 'lucide-react';
import { cn } from '../../utils/cn';

interface BreadcrumbItem {
  label: string;
  path: string;
}

interface WorkspaceBreadcrumbProps {
  items: BreadcrumbItem[];
  onNavigate: (path: string) => void;
}

export default function WorkspaceBreadcrumb({ items, onNavigate }: WorkspaceBreadcrumbProps) {
  return (
    <nav className="flex items-center gap-1 text-sm min-w-0 overflow-hidden" aria-label="路径导航">
      {items.map((item, index) => {
        const isLast = index === items.length - 1;
        return (
          <span key={item.path} className="flex items-center gap-1 min-w-0">
            {index > 0 && (
              <ChevronRight className="w-3.5 h-3.5 text-[var(--s-text-tertiary)] shrink-0" />
            )}
            <button
              type="button"
              onClick={() => onNavigate(item.path)}
              disabled={isLast}
              className={cn(
                'truncate max-w-[120px]',
                isLast
                  ? 'text-[var(--s-text-primary)] font-medium cursor-default'
                  : 'text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)] transition-colors',
              )}
            >
              {item.label}
            </button>
          </span>
        );
      })}
    </nav>
  );
}

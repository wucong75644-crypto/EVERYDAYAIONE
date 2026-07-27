import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ScheduledTaskPanel from '../ScheduledTaskPanel';

const fetchTasks = vi.fn();
let storeState = {
  tasks: [],
  loading: false,
  error: null as string | null,
  fetchTasks,
};

vi.mock('../../../stores/useScheduledTaskStore', () => ({
  useScheduledTaskStore: (selector: (state: typeof storeState) => unknown) =>
    selector(storeState),
}));

vi.mock('framer-motion', () => ({
  AnimatePresence: ({ children }: { children: ReactNode }) => <>{children}</>,
  m: {
    button: (props: React.ButtonHTMLAttributes<HTMLButtonElement> & {
      whileHover?: unknown;
      whileTap?: unknown;
      transition?: unknown;
    }) => {
      const { children, whileHover, whileTap, transition, ...buttonProps } = props;
      void whileHover;
      void whileTap;
      void transition;
      return <button {...buttonProps}>{children}</button>;
    },
    div: ({ children, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
      <div {...props}>{children}</div>
    ),
    aside: ({ children, ...props }: React.HTMLAttributes<HTMLElement>) => (
      <aside {...props}>{children}</aside>
    ),
  },
}));

vi.mock('../ViewSwitcher', () => ({ ViewSwitcher: () => null }));
vi.mock('../TaskList', () => ({
  TaskList: () => <div data-testid="task-list">任务列表</div>,
}));
vi.mock('../TaskForm', () => ({ TaskForm: () => null }));

describe('ScheduledTaskPanel 加载状态', () => {
  beforeEach(() => {
    fetchTasks.mockClear();
    storeState = {
      tasks: [],
      loading: false,
      error: null,
      fetchTasks,
    };
  });

  it('接口失败时显示错误和重试入口，不伪装成空任务', () => {
    storeState.error = '加载定时任务失败';

    render(<ScheduledTaskPanel isOpen onClose={vi.fn()} />);

    expect(screen.getByRole('alert')).toHaveTextContent('加载定时任务失败');
    expect(screen.queryByTestId('task-list')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(fetchTasks).toHaveBeenCalledTimes(2);
  });

  it('正常状态继续渲染任务列表', () => {
    render(<ScheduledTaskPanel isOpen onClose={vi.fn()} />);

    expect(screen.getByTestId('task-list')).toBeInTheDocument();
  });
});

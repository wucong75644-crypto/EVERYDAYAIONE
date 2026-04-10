/**
 * Popover primitive 测试
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Popover, PopoverClose } from '../Popover';

describe('Popover', () => {
  it('初始不显示内容', () => {
    render(
      <Popover trigger={<button>open</button>}>
        <div>popover body</div>
      </Popover>,
    );
    expect(screen.queryByText('popover body')).not.toBeInTheDocument();
  });

  it('点击 trigger 后显示', () => {
    render(
      <Popover trigger={<button>open</button>}>
        <div>popover body</div>
      </Popover>,
    );
    fireEvent.click(screen.getByText('open'));
    expect(screen.getByText('popover body')).toBeInTheDocument();
  });

  it('受控 open={true} 时直接显示', () => {
    render(
      <Popover
        trigger={<button>open</button>}
        open={true}
        onOpenChange={vi.fn()}
      >
        <div>always</div>
      </Popover>,
    );
    expect(screen.getByText('always')).toBeInTheDocument();
  });

  it('onOpenChange 在 open 切换时被调用', () => {
    const handleChange = vi.fn();
    render(
      <Popover trigger={<button>trigger</button>} onOpenChange={handleChange}>
        <div>body</div>
      </Popover>,
    );
    fireEvent.click(screen.getByText('trigger'));
    expect(handleChange).toHaveBeenCalledWith(true);
  });

  it('PopoverClose 按钮能触发关闭', () => {
    const handleChange = vi.fn();
    render(
      <Popover
        trigger={<button>open</button>}
        open={true}
        onOpenChange={handleChange}
      >
        <div>
          body
          <PopoverClose asChild>
            <button>close</button>
          </PopoverClose>
        </div>
      </Popover>,
    );
    fireEvent.click(screen.getByText('close'));
    expect(handleChange).toHaveBeenCalledWith(false);
  });
});

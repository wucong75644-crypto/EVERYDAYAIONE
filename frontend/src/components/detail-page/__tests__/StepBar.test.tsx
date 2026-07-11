import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StepBar } from '../StepBar';

describe('StepBar', () => {
  it('显示五个固定步骤', () => {
    render(<StepBar step={1} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByText('确认规划')).toBeInTheDocument();
  });

  it('标记当前步骤', () => {
    render(<StepBar step={3} />);
    expect(screen.getByText('确认规划').closest('li')).toHaveAttribute('aria-current', 'step');
  });
});

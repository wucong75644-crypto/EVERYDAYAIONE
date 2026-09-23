import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DraftContent } from '../../../services/skillAdmin';
import { SkillToolPolicy } from '../skills/SkillToolPolicy';

const legacy: DraftContent = { body: '原正文', description: '原用途',
  catalog_metadata: { name: '旧 Skill', allowed_tool_names: [], required_permissions: ['orders.read'] } };

describe('Skill tool policy compatibility', () => {
  it('explains the old empty ceiling without silently changing the draft', () => {
    const onChange = vi.fn();
    render(<SkillToolPolicy content={legacy} busy={false} onChange={onChange} />);
    expect(screen.getByText(/不能调用工具/)).toBeVisible();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '改用当前会话权限' }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith({ ...legacy, catalog_metadata: {
      ...legacy.catalog_metadata, tool_policy: 'platform', allowed_tool_names: [],
    } });
    expect(legacy.catalog_metadata.tool_policy).toBeUndefined();
  });

  it('keeps an existing tool restriction until the editor explicitly changes it', () => {
    const onChange = vi.fn();
    render(<SkillToolPolicy content={{ ...legacy, catalog_metadata: { allowed_tool_names: ['file_search'] } }} busy onChange={onChange} />);
    expect(screen.getByText(/仅可使用已配置的 1 个工具/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '改用当前会话权限' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '改用当前会话权限' })).toBeDisabled();
  });

  it('shows host authorization for a new draft without asking for tool names', () => {
    render(<SkillToolPolicy content={{ ...legacy, catalog_metadata: { tool_policy: 'platform' } }} busy={false} onChange={vi.fn()} />);
    expect(screen.getByText(/组织、成员权限和操作审批仍然生效/)).toBeVisible();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });
});

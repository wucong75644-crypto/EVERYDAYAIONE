import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { DraftContent } from '../../../services/skillAdmin';
import { SkillDraftEditor } from '../skills/SkillDraftEditor';

const plain: DraftContent = { description: '验证模板', body: '请按以下格式输出。', catalog_metadata: {} };
function Editor({ initial = plain, busy = false }: { initial?: DraftContent; busy?: boolean }) {
  const [content, setContent] = useState(initial);
  return <><SkillDraftEditor content={content} busy={busy} dirty={false} onChange={setContent} />
    <output aria-label="saved content">{JSON.stringify(content)}</output></>;
}
const saved = (): DraftContent => JSON.parse(screen.getByLabelText('saved content').textContent!);

describe('Skill template authoring', () => {
  it('leaves plain instructions and unused dynamic settings alone', () => {
    render(<Editor />);
    expect(screen.queryByRole('button', { name: '启用这些信息' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Skill 操作说明'), { target: { value: '直接用中文列出结论。' } });
    expect(saved()).toEqual({ ...plain, body: '直接用中文列出结论。' });
    expect(screen.getByRole('combobox', { name: 'Skill 操作说明：插入动态信息' })).toBeVisible();
  });

  it('inserts selected information at the cursor with its explicit typed declaration', () => {
    render(<Editor initial={{ ...plain, body: '前面后面' }} />);
    const input = screen.getByLabelText('Skill 操作说明') as HTMLTextAreaElement;
    input.focus(); input.setSelectionRange(2, 2); fireEvent.select(input);
    fireEvent.change(screen.getByRole('combobox', { name: 'Skill 操作说明：插入动态信息' }), { target: { value: 'is_channel' } });
    expect(saved().body).toBe('前面{{args.is_channel}}后面');
    expect(saved().template_variables).toEqual({ is_channel: { type: 'boolean', source: 'is_channel' } });
    expect(input).toHaveFocus();
    fireEvent.change(screen.getByRole('combobox', { name: 'Skill 操作说明：插入动态信息' }), { target: { value: 'conversation_scope' } });
    expect(saved().body).toBe('前面{{args.is_channel}}{{args.conversation_scope}}后面');
    expect(saved().template_variables).toEqual({ is_channel: { type: 'boolean', source: 'is_channel' },
      conversation_scope: { type: 'string', source: 'conversation_scope' } });
  });

  it('repairs known placeholders only after a click, preserving unrelated declarations and literal reference text', () => {
    const initial: DraftContent = { ...plain, body: '{{args.is_channel}} {{args.secret_path}}',
      template_variables: { organization: { source: 'org_id', type: 'string' } }, assets: [
        { id: 'template', name: '模板', summary: '模板', kind: 'template', format: 'txt', content: '{{args.conversation_scope}}' },
        { id: 'reference', name: '参考', summary: '参考', kind: 'reference', format: 'txt', content: '{{args.actor_user_id}}' },
      ] };
    render(<Editor initial={initial} />);
    expect(saved()).toEqual(initial);
    fireEvent.click(screen.getByRole('button', { name: '启用这些信息' }));
    expect(saved().template_variables).toEqual({ ...initial.template_variables,
      is_channel: { source: 'is_channel', type: 'boolean' }, conversation_scope: { source: 'conversation_scope', type: 'string' } });
    expect(saved().body).toBe(initial.body);
    expect(saved().assets).toEqual(initial.assets);
    expect(screen.getByText(/无法识别的动态信息：secret_path/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启用这些信息' })).not.toBeInTheDocument();
  });

  it('does not overwrite a custom mapping or exceed the declaration limit', () => {
    const variables: NonNullable<DraftContent['template_variables']> = Object.fromEntries(Array.from({ length: 15 }, (_, i) => [`custom_${i}`, { source: 'org_id', type: 'string' }]));
    variables.conversation_scope = { source: 'org_id', type: 'string' };
    render(<Editor initial={{ ...plain, body: '{{args.is_channel}}', template_variables: variables }} />);
    expect(screen.getByRole('button', { name: '启用这些信息' })).toBeDisabled();
    const menu = screen.getByRole('combobox', { name: 'Skill 操作说明：插入动态信息' });
    fireEvent.change(menu, { target: { value: 'conversation_scope' } });
    fireEvent.change(menu, { target: { value: 'is_channel' } });
    expect(saved().template_variables).toEqual(variables);
    expect(saved().body).toBe('{{args.is_channel}}');
  });

  it('disables editing helpers while a save is in progress', () => {
    render(<Editor initial={{ ...plain, body: '{{args.is_channel}}' }} busy />);
    expect(screen.getByRole('button', { name: '启用这些信息' })).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Skill 操作说明：插入动态信息' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '添加附件' })).toBeDisabled();
  });
});

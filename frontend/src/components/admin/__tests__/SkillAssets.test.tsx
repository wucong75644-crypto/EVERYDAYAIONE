import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { DraftContent, SkillAssetSummary } from '../../../services/skillAdmin';
import { SkillAssetEditor, SkillAssets, SkillTemplateEditor } from '../skills/SkillAssets';

describe('Skill attachments', () => {
  it('shows bounded public summaries without paths, hashes or attachment bodies', () => {
    const asset = { id: 'report', name: '<script>hello</script>', kind: 'template', summary: '月度报告模板',
      format: 'md', bytes: 123, path: '/private/nas/report.md', sha256: 'secret-hash', content: 'private attachment body' };
    const { container } = render(<SkillAssets assets={[asset as SkillAssetSummary]} />);
    expect(screen.getByText('月度报告模板')).toBeInTheDocument();
    expect(screen.getByText('<script>hello</script>')).toBeInTheDocument();
    expect(screen.getByText(/模板 · MD · 123 字节/)).toBeInTheDocument();
    expect(container.querySelector('script, a, img')).toBeNull();
    expect(container.textContent).not.toMatch(/private\/nas|secret-hash|private attachment body/);
  });

  it('creates and references a template without typing identifiers or configuring variable types', () => {
    function Editor() {
      const [content, setContent] = useState<DraftContent>({ body: '', description: '', catalog_metadata: {} });
      return <>
        <SkillAssetEditor content={content} busy={false} onChange={setContent} />
        <SkillTemplateEditor content={content} busy={false} onChange={setContent} />
        <output aria-label="saved content">{JSON.stringify(content)}</output>
      </>;
    }
    render(<Editor />);
    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    expect(screen.getByLabelText('附件名称')).toBeVisible();
    fireEvent.change(screen.getByLabelText('附件名称'), { target: { value: '报告模板' } });
    fireEvent.change(screen.getByLabelText('附件用途'), { target: { value: '月度报告格式' } });
    fireEvent.change(screen.getByLabelText('附件类型'), { target: { value: 'template' } });
    fireEvent.change(screen.getByLabelText('附件内容'), { target: { value: '组织 ' } });
    fireEvent.change(screen.getByRole('combobox', { name: '附件内容：插入动态信息' }), { target: { value: 'org_id' } });
    fireEvent.click(screen.getByRole('button', { name: '在操作说明中引用' }));
    const saved = JSON.parse(screen.getByLabelText('saved content').textContent!);
    expect(saved.assets).toEqual([{ id: 'attachment-1', name: '报告模板', summary: '月度报告格式', kind: 'template',
      format: 'md', content: '组织 {{args.org_id}}' }]);
    expect(saved.template_variables).toEqual({ org_id: { type: 'string', source: 'org_id' } });
    expect(saved.body).toBe('请参考附件 [[asset:attachment-1]]。');
    expect(screen.getByRole('button', { name: '已在操作说明中引用' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '移除附件' }));
    expect(JSON.parse(screen.getByLabelText('saved content').textContent!).assets).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: '添加附件' }));
    expect(JSON.parse(screen.getByLabelText('saved content').textContent!).assets[0].id).toBe('attachment-2');
    expect(screen.getByRole('button', { name: '在操作说明中引用' })).toBeEnabled();
  });

  it('keeps review content read only and caps the attachment count', () => {
    const content: DraftContent = { body: '', description: '', catalog_metadata: {}, assets: Array.from({ length: 16 }, (_, i) => ({
      id: `ref-${i}`, name: `参考 ${i}`, summary: '用途', format: 'txt', kind: 'reference', content: '内容',
    })) };
    render(<SkillAssetEditor content={content} busy onChange={() => { throw Error('read only'); }} />);
    expect(screen.getByRole('button', { name: '添加附件' })).toBeDisabled();
    fireEvent.click(screen.getByText('参考 0 · 只读参考资料'));
    expect(screen.getAllByLabelText('附件内容')[0]).toBeDisabled();
  });
});

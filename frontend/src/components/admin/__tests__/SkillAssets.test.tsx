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

  it('edits assets by stable IDs and supplies explicit server variable types', () => {
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
    fireEvent.click(screen.getByText('附件 1 · 只读参考资料'));
    fireEvent.change(screen.getByLabelText('附件标识'), { target: { value: 'report' } });
    fireEvent.change(screen.getByLabelText('附件名称'), { target: { value: '报告模板' } });
    fireEvent.change(screen.getByLabelText('附件用途'), { target: { value: '月度报告格式' } });
    fireEvent.change(screen.getByLabelText('附件类型'), { target: { value: 'template' } });
    fireEvent.change(screen.getByLabelText('附件内容'), { target: { value: '组织 {{args.org_id}}' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /当前组织 ID/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /是否群组会话/ }));
    const saved = JSON.parse(screen.getByLabelText('saved content').textContent!);
    expect(saved.assets).toEqual([{ id: 'report', name: '报告模板', summary: '月度报告格式', kind: 'template',
      format: 'md', content: '组织 {{args.org_id}}' }]);
    expect(saved.template_variables).toEqual({ org_id: { type: 'string', source: 'org_id' },
      is_channel: { type: 'boolean', source: 'is_channel' } });
    expect(screen.getByText('[[asset:report]]')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '移除附件' }));
    expect(JSON.parse(screen.getByLabelText('saved content').textContent!).assets).toEqual([]);
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

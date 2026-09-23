import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DraftContent, SkillAssetDraft, SkillAssetSummary } from '../../../services/skillAdmin';
import { SkillAssetEditor, SkillAssets } from '../skills/SkillAssets';

const uploaded: SkillAssetDraft = { id: 'attachment-one', name: '日报.docx', kind: 'reference', summary: '参考资料：日报.docx',
  format: 'txt', content: '已读取的内容', source: { format: 'docx', base64: 'ZmlsZQ==' } };

describe('Skill attachments', () => {
  it('shows bounded public summaries without paths, hashes or attachment bodies', () => {
    const asset = { id: 'report', name: '<script>hello</script>', kind: 'template', summary: '月度报告模板',
      format: 'txt', bytes: 123, file_format: 'docx', file_bytes: 1000,
      path: '/private/nas/report.md', sha256: 'secret-hash', content: 'private attachment body' };
    const { container } = render(<SkillAssets assets={[asset as SkillAssetSummary]} />);
    expect(screen.getByText('月度报告模板')).toBeInTheDocument();
    expect(screen.getByText('<script>hello</script>')).toBeInTheDocument();
    expect(screen.getByText(/模板 · DOCX · 1000 字节/)).toBeInTheDocument();
    expect(container.querySelector('script, a, img')).toBeNull();
    expect(container.textContent).not.toMatch(/private\/nas|secret-hash|private attachment body/);
  });

  it('selects actual files and resets the input so the same file can be selected again', () => {
    const onUpload = vi.fn();
    render(<SkillAssetEditor content={{ body: '', description: '', catalog_metadata: {} }} busy={false} onChange={vi.fn()} onUpload={onUpload} />);
    expect(screen.queryByLabelText('附件名称')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('附件内容')).not.toBeInTheDocument();
    const input = screen.getByLabelText('选择附件文件');
    const click = vi.spyOn(input, 'click');
    fireEvent.click(screen.getByRole('button', { name: '上传附件' }));
    expect(click).toHaveBeenCalledOnce();
    const file = new File(['file'], '日报.docx');
    fireEvent.change(input, { target: { files: [file] } });
    expect(onUpload).toHaveBeenCalledWith([file]);
    expect(input).toHaveValue('');
  });

  it('shows a compact uploaded row, references it and removes the reference with the file', () => {
    function Editor() {
      const [content, setContent] = useState<DraftContent>({ body: '完成日报。', description: '', catalog_metadata: {}, assets: [uploaded] });
      return <><SkillAssetEditor content={content} busy={false} onChange={setContent} onUpload={vi.fn()} />
        <output aria-label="saved content">{JSON.stringify(content)}</output></>;
    }
    render(<Editor />);
    expect(screen.getByText('日报.docx')).toBeVisible();
    expect(screen.getByLabelText('附件名称')).not.toBeVisible();
    expect(screen.getByLabelText('读取内容（预览）')).not.toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '在操作说明中引用' }));
    expect(JSON.parse(screen.getByLabelText('saved content').textContent!).body).toContain('[[asset:attachment-one]]');
    expect(screen.getByRole('button', { name: '已在操作说明中引用' })).toBeDisabled();
    fireEvent.click(screen.getByText('查看内容与设置'));
    expect(screen.getByLabelText('读取内容（预览）')).toHaveAttribute('readonly');
    fireEvent.change(screen.getByLabelText('附件类型'), { target: { value: 'template' } });
    expect(JSON.parse(screen.getByLabelText('saved content').textContent!).assets[0].kind).toBe('template');
    fireEvent.click(screen.getByRole('button', { name: '移除附件' }));
    const saved = JSON.parse(screen.getByLabelText('saved content').textContent!);
    expect(saved.assets).toEqual([]);
    expect(saved.body).toBe('完成日报。');
  });

  it('keeps existing text templates editable without exposing their form by default', () => {
    const content: DraftContent = { body: '', description: '', catalog_metadata: {}, assets: [{ ...uploaded, source: null, kind: 'template' }] };
    const onChange = vi.fn();
    render(<SkillAssetEditor content={content} busy={false} onChange={onChange} />);
    expect(screen.getByLabelText('附件内容')).not.toBeVisible();
    fireEvent.click(screen.getByText('查看内容与设置'));
    fireEvent.change(screen.getByLabelText('附件内容'), { target: { value: '新格式' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ assets: [expect.objectContaining({ content: '新格式' })] }));
  });

  it('removes the previous auto-reference sentence from an existing draft', () => {
    const onChange = vi.fn();
    render(<SkillAssetEditor content={{ body: '完成日报。\n\n请参考附件 [[asset:attachment-one]]。',
      description: '', catalog_metadata: {}, assets: [uploaded] }} busy={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: '移除附件' }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ body: '完成日报。', assets: [] }));
  });

  it('keeps review content read only and caps the attachment count', () => {
    const content: DraftContent = { body: '', description: '', catalog_metadata: {}, assets: Array.from({ length: 16 }, (_, i) => ({ ...uploaded, id: `ref-${i}` })) };
    render(<SkillAssetEditor content={content} busy onChange={() => { throw Error('read only'); }} onUpload={vi.fn()} />);
    expect(screen.getByRole('button', { name: '上传附件' })).toBeDisabled();
    expect(screen.getByLabelText('选择附件文件')).toBeDisabled();
    expect(screen.getAllByRole('button', { name: '移除附件' })[0]).toBeDisabled();
  });
});

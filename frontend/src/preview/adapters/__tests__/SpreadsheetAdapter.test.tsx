import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { spreadsheetAdapter } from '../SpreadsheetAdapter';

const { fetchPreviewResponseMock, sheetToJsonMock, readMock } = vi.hoisted(() => ({
  fetchPreviewResponseMock: vi.fn(),
  sheetToJsonMock: vi.fn(),
  readMock: vi.fn(),
}));

vi.mock('../../fetchPreview', () => ({
  fetchPreviewResponse: fetchPreviewResponseMock,
}));

vi.mock('../../PreviewFrame', () => ({
  default: ({ children, error, footer }: { children: React.ReactNode; error?: string | null; footer?: React.ReactNode }) => (
    <div>{error || children}{footer}</div>
  ),
}));

vi.mock('xlsx', () => ({
  read: readMock,
  utils: { sheet_to_json: sheetToJsonMock },
}));

describe('SpreadsheetAdapter', () => {
  beforeEach(() => {
    readMock.mockReturnValue({ SheetNames: ['Sheet1'], Sheets: { Sheet1: {} } });
  });

  it('renders non-string Excel cells through the safe display boundary', async () => {
    fetchPreviewResponseMock.mockResolvedValue({
      response: { arrayBuffer: vi.fn().mockResolvedValue(new ArrayBuffer(1)) },
    });
    sheetToJsonMock.mockReturnValue([
      ['value'],
      [{ answer: 42 }],
      [10n],
    ]);
    const Component = spreadsheetAdapter.Component;

    render(
      <Component
        item={{ filename: 'data.xlsx', url: '/data.xlsx' }}
        siblings={[]}
        index={0}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByText('{"answer":42}')).toBeInTheDocument());
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('parses quoted CSV and TSV data', async () => {
    fetchPreviewResponseMock.mockResolvedValue({
      response: { text: vi.fn().mockResolvedValue('name,note\r\nA,"hello,world"\r\n') },
    });
    const Component = spreadsheetAdapter.Component;
    const { rerender } = render(
      <Component item={{ filename: 'data.csv' }} siblings={[]} index={0}
        onClose={vi.fn()} onNavigate={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByText('hello,world')).toBeInTheDocument());

    fetchPreviewResponseMock.mockResolvedValue({
      response: { text: vi.fn().mockResolvedValue('name\tvalue\nA\t1') },
    });
    rerender(
      <Component item={{ filename: 'data.tsv' }} siblings={[]} index={0}
        onClose={vi.fn()} onNavigate={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByText('1')).toBeInTheDocument());
  });

  it('shows empty state and loading failures', async () => {
    fetchPreviewResponseMock.mockResolvedValueOnce({
      response: { text: vi.fn().mockResolvedValue('header') },
    });
    const Component = spreadsheetAdapter.Component;
    const { rerender } = render(
      <Component item={{ filename: 'empty.csv' }} siblings={[]} index={0}
        onClose={vi.fn()} onNavigate={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByText('暂无数据')).toBeInTheDocument());

    fetchPreviewResponseMock.mockRejectedValueOnce(new Error('加载失败'));
    rerender(
      <Component item={{ filename: 'broken.csv' }} siblings={[]} index={0}
        onClose={vi.fn()} onNavigate={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByText('加载失败')).toBeInTheDocument());
  });

  it('switches between workbook sheets', async () => {
    fetchPreviewResponseMock.mockResolvedValue({
      response: { arrayBuffer: vi.fn().mockResolvedValue(new ArrayBuffer(1)) },
    });
    readMock.mockReturnValue({
      SheetNames: ['Sheet1', 'Sheet2'],
      Sheets: { Sheet1: {}, Sheet2: {} },
    });
    sheetToJsonMock
      .mockReturnValueOnce([['value'], ['first']])
      .mockReturnValueOnce([['value'], ['second']]);
    const Component = spreadsheetAdapter.Component;
    render(
      <Component item={{ filename: 'multi.xlsx' }} siblings={[]} index={0}
        onClose={vi.fn()} onNavigate={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByText('first')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Sheet2' }));
    await waitFor(() => expect(screen.getByText('second')).toBeInTheDocument());
  });
});

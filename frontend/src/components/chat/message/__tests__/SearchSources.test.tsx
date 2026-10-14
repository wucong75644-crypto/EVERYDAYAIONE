import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import SearchSources from '../SearchSources';
import { splitSearchSources } from '../parseSearchSources';

const marker = '\n\n检索来源（未与正文逐句对应）：\n';
describe('search sources appendix', () => {
  it('retains titles, falls back to domains, and deduplicates legacy URLs', () => {
    const result = splitSearchSources(`回答${marker}- [案例\\[一\\]](<https://www.example.com/a>)\n- <https://other.com/b>\n- <https://other.com/b>`);
    expect(result.body).toBe('回答');
    expect(result.sources.map(s => [s.title, s.domain])).toEqual([['案例[一]', 'example.com'], ['other.com', 'other.com']]);
  });
  it('does not consume ordinary links, code samples or unsafe appendices', () => {
    for (const content of ['正文 [来源](https://example.com)', `\`\`\`text${marker}- <https://example.com>\n\`\`\``, `回答${marker}- <javascript:alert(1)>`, `回答${marker}- <https://example.com>\n后续正文`]) {
      expect(splitSearchSources(content)).toEqual({ body: content, sources: [] });
    }
  });
  it('hides the unfinished last source while streaming', () => {
    const result = splitSearchSources(`回答${marker}- <https://example.com>\n- [未完成](<https://other`, true);
    expect(result.body).toBe('回答');
    expect(result.sources).toHaveLength(1);
  });
  it('starts collapsed and exposes five sources with a more button', () => {
    const { sources } = splitSearchSources(`回答${marker}${Array.from({ length: 7 }, (_, i) => `- [标题 ${i}](<https://example.com/${i}>)`).join('\n')}`);
    const { container } = render(<SearchSources sources={sources} />);
    const details = container.querySelector('details')!;
    expect(details.open).toBe(false);
    fireEvent.click(screen.getByText('检索来源 · 7 个'));
    expect(details.open).toBe(true);
    expect(screen.getAllByRole('link')).toHaveLength(5);
    expect(screen.getByRole('link', { name: /标题 0/ })).toHaveAttribute('href', 'https://example.com/0');
    fireEvent.click(screen.getByRole('button', { name: '查看其余 2 个来源' }));
    expect(screen.getAllByRole('link')).toHaveLength(7);
  });
});

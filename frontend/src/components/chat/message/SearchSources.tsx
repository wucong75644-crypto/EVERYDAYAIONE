import { useState } from 'react';
import type { SearchSource } from './parseSearchSources';

export default function SearchSources({ sources }: { sources: SearchSource[] }) {
  const [showAll, setShowAll] = useState(false);
  const domains = [...new Set(sources.map(source => source.domain))];
  return (
    <details className="search-sources not-prose whitespace-normal mt-4 border-t border-gray-200 pt-3 text-sm text-gray-500">
      <summary className="cursor-pointer select-none rounded py-1 focus-visible:outline focus-visible:outline-2">
        <span className="font-medium text-gray-600">检索来源 · {sources.length} 个</span>
        <span className="search-sources-open ml-3 text-xs">展开</span>
        <span className="search-sources-close ml-3 text-xs">收起</span>
        <span className="mt-1 block truncate pl-4 text-xs">{domains.slice(0, 3).join(' · ')}{domains.length > 3 ? ' 等' : ''}</span>
      </summary>
      <p className="mt-2 text-xs">检索过程中返回的网页，未与正文逐句对应。</p>
      <ol className="mt-2 space-y-1">
        {(showAll ? sources : sources.slice(0, 5)).map((source, index) => (
          <li key={source.url}>
            <a href={source.url} target="_blank" rel="noopener noreferrer" title={source.url}
              className="flex min-w-0 gap-3 rounded px-2 py-2 hover:bg-gray-50 focus-visible:outline focus-visible:outline-2">
              <span className="pt-0.5 text-xs tabular-nums">{String(index + 1).padStart(2, '0')}</span>
              <span className="min-w-0 flex-1">
                <span className="line-clamp-2 break-words text-gray-800">{source.title}</span>
                <span className="mt-0.5 block truncate text-xs">{source.domain}</span>
              </span>
              <span aria-hidden="true">↗</span>
            </a>
          </li>
        ))}
      </ol>
      {!showAll && sources.length > 5 && <button type="button" onClick={() => setShowAll(true)}
        className="mt-2 rounded px-2 py-2 text-sm text-blue-600 hover:underline">查看其余 {sources.length - 5} 个来源</button>}
    </details>
  );
}

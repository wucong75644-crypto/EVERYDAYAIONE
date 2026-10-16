export interface SearchSource { url: string; title: string; domain: string }

/** Recognize only the application's trailing source appendix, including old messages. */
export function splitSearchSources(content: string, incompleteTail = false): { body: string; sources: SearchSource[] } {
  const marker = '\n\n检索来源（未与正文逐句对应）：\n';
  const start = content.lastIndexOf(marker);
  if (start < 0 || content.slice(0, start).split('```').length % 2 === 0) {
    return { body: content, sources: [] };
  }
  const sources: SearchSource[] = [];
  const seen = new Set<string>();
  const lines = content.slice(start + marker.length).trim().split('\n');
  for (const [index, line] of lines.entries()) {
    const match = line.match(/^- (?:<([^<>]+)>|\[((?:\\.|[^\\\]])*)\]\(<([^<>]+)>\))$/);
    if (!match) {
      if (incompleteTail && index === lines.length - 1) break;
      return { body: content, sources: [] };
    }
    const url = match[1] || match[3];
    try {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
        return { body: content, sources: [] };
      }
      if (seen.has(url)) continue;
      seen.add(url);
      const domain = parsed.hostname.replace(/^www\./, '');
      const label = match[2]?.replace(/\\(.)/g, '$1').trim();
      sources.push({ url, domain, title: !label || label === url ? domain : label });
    } catch {
      return { body: content, sources: [] };
    }
  }
  return { body: content.slice(0, start), sources };
}


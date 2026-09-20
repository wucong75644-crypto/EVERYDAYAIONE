import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** Skill text is untrusted: no HTML execution, embeds or automatic remote images. */
export function SkillDocument({ body }: { body: string }) {
  return <article className="min-h-56 break-words text-sm leading-7 text-[var(--s-text-secondary)] [&_h1]:text-xl [&_h2]:text-lg [&_h3]:text-base [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-medium [&_h1]:text-[var(--s-text-primary)] [&_h2]:text-[var(--s-text-primary)] [&_h3]:text-[var(--s-text-primary)] [&_h1]:mb-4 [&_h2]:mt-6 [&_h2]:mb-2 [&_h3]:mt-4 [&_p]:my-3 [&_ul]:list-disc [&_ol]:list-decimal [&_ul]:pl-6 [&_ol]:pl-6 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-[var(--s-surface-sunken)] [&_pre]:p-4 [&_code]:font-mono [&_code]:text-xs [&_blockquote]:border-l-2 [&_blockquote]:border-[var(--s-border-default)] [&_blockquote]:pl-4 [&_hr]:my-5 [&_hr]:border-[var(--s-border-default)]">
    {body ? <Markdown remarkPlugins={[remarkGfm]} components={{
      a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-[var(--s-accent)] underline underline-offset-2">{children}</a>,
      img: ({ alt }) => <span className="text-[var(--s-text-tertiary)]">[图片：{alt || '未命名'}]</span>,
      table: ({ children }) => <div className="overflow-x-auto"><table className="w-full text-left [&_th]:border-b [&_td]:border-b [&_th]:border-[var(--s-border-default)] [&_td]:border-[var(--s-border-default)] [&_th]:p-2 [&_td]:p-2">{children}</table></div>,
    }}>{body}</Markdown> : <p className="text-[var(--s-text-tertiary)]">尚未填写操作说明。</p>}
  </article>;
}

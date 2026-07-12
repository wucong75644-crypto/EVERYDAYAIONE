import { useEffect, useState } from 'react';
import { Folder, Search } from 'lucide-react';
import Modal from '../common/Modal';
import { Button } from '../ui/Button';
import { getWorkspacePreviewUrl, listWorkspace, searchWorkspace, type WorkspaceFileItem } from '../../services/workspace';

interface PickerItem extends WorkspaceFileItem { workspacePath: string }
interface Props { open: boolean; remaining: number; onClose: () => void; onSelect: (paths: string[]) => void }
const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'webp']);
const joinPath = (dir: string, name: string) => dir === '.' ? name : `${dir.replace(/\/$/, '')}/${name}`;

export function WorkspaceImagePicker({ open, remaining, onClose, onSelect }: Props) {
  const [path, setPath] = useState('.');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<PickerItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    let active = true;
    const load = query.trim()
      ? searchWorkspace(query.trim(), 50).then((result) => result.items.map((item) => ({ ...item, workspacePath: item.workspace_path || item.name })))
      : listWorkspace(path).then((result) => result.items.map((item) => ({ ...item, workspacePath: joinPath(result.path, item.name) })));
    setError(null);
    void load.then((next) => { if (active) setItems(next); }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : '工作区加载失败'); });
    return () => { active = false; };
  }, [open, path, query]);
  const visible = items.filter((item) => item.is_dir || IMAGE_EXTENSIONS.has(item.name.split('.').pop()?.toLowerCase() || ''));
  const toggle = (value: string) => setSelected((current) => current.includes(value) ? current.filter((item) => item !== value) : current.length < remaining ? [...current, value] : current);
  return <Modal isOpen={open} onClose={onClose} title="从工作区选择图片" maxWidth="max-w-4xl">
    <div className="flex gap-2"><label className="flex-1 flex items-center gap-2 px-3 border rounded-lg"><Search className="w-4 h-4" /><input aria-label="搜索工作区图片" value={query} onChange={(event) => setQuery(event.target.value)} className="w-full py-2 bg-transparent outline-none" placeholder="搜索图片名称" /></label>{path !== '.' && !query && <Button variant="secondary" onClick={() => setPath(path.split('/').slice(0, -1).join('/') || '.')}>上一级</Button>}</div>
    <p className="mt-3 text-xs text-[var(--s-text-tertiary)]">当前位置：{path}，还可选择 {remaining} 张</p>
    {error && <p role="alert" className="mt-3 text-sm text-[var(--s-error)]">{error}</p>}
    <div className="mt-4 grid grid-cols-3 sm:grid-cols-5 gap-3 max-h-[420px] overflow-auto">{visible.map((item) => item.is_dir ? <button key={item.workspacePath} type="button" onClick={() => { setQuery(''); setPath(item.workspacePath); }} className="aspect-square border rounded-lg flex flex-col items-center justify-center gap-2"><Folder className="w-8 h-8" /><span className="text-xs truncate max-w-full px-2">{item.name}</span></button> : <button key={item.workspacePath} type="button" aria-pressed={selected.includes(item.workspacePath)} onClick={() => toggle(item.workspacePath)} className="aspect-square border rounded-lg overflow-hidden aria-pressed:ring-2"><img src={item.thumbnail_url || item.cdn_url || getWorkspacePreviewUrl(item.workspacePath)} alt={item.name} className="w-full h-full object-cover" /></button>)}</div>
    {!visible.length && !error && <p className="py-12 text-center text-sm text-[var(--s-text-tertiary)]">没有可选图片</p>}
    <div className="mt-5 flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>取消</Button><Button disabled={!selected.length} onClick={() => { onSelect(selected); setSelected([]); onClose(); }}>添加 {selected.length || ''}</Button></div>
  </Modal>;
}

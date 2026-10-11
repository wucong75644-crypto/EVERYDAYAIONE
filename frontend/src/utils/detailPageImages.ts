import type { DetailGroup, DetailImageTask } from '../types/detailPage';

export function detailTaskFor(group: DetailGroup, itemId: string): DetailImageTask | undefined {
  return group.tasks.filter(task => task.item_id === itemId).at(-1);
}

/** Download the same latest version shown in the grid, using workspace originals. */
export function detailImageDownloads(groups: DetailGroup[]): Array<{ path: string; archivePath: string }> {
  return [...groups]
    .sort((a, b) => Number(a.kind === 'detail_page') - Number(b.kind === 'detail_page'))
    .flatMap(group => [...group.items].sort((a, b) => a.position - b.position).flatMap(item => {
      const task = detailTaskFor(group, item.item_id);
      const path = task?.result_data?.workspace_path;
      if (task?.status !== 'completed' || !path) return [];
      const extension = /\.[a-z0-9]+$/i.exec(path)?.[0];
      if (!extension) return [];
      const name = item.name.replace(/[\\/:*?"<>|]/g, '_').split('')
        .map(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 ? '_' : char)
        .join('').trim().slice(0, 80) || '图片';
      const folder = group.kind === 'main_images' ? '主图' : '详情页';
      return [{ path, archivePath: `${folder}/${String(item.position).padStart(2, '0')}-${name}${extension}` }];
    }));
}

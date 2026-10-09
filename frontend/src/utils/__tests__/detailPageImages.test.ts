import { describe, expect, it } from 'vitest';
import type { DetailGroup } from '../../types/detailPage';
import { detailImageDownloads } from '../detailPageImages';

function group(kind: DetailGroup['kind']): DetailGroup {
  return {
    plan_id: kind, kind, count: 3, stage: 3, status: 'ready',
    items: [3, 1, 2].map(position => ({item_id: String(position), position, name: '商品/细节:*?',
      purpose: '展示', request_text: '正文', aspect_ratio: '1:1'})),
    tasks: [2, 3, 1].map(position => ({id: `task-${position}`, item_id: String(position),
      status: position === 2 ? 'pending' : 'completed', submission_state: 'accepted', created_at: '2026-10-09',
      result_data: {workspace_path: `images/${position}.webp`, thumbnail_url: 'https://cdn/thumbnail.png'}})),
  };
}

describe('主图详情批量下载清单', () => {
  it('按分类与位置排列，仅下载已完成原图，并清理文件名中的路径字符', () => {
    expect(detailImageDownloads([group('detail_page'), group('main_images')])).toEqual([
      {path: 'images/1.webp', archivePath: '主图/01-商品_细节___.webp'},
      {path: 'images/3.webp', archivePath: '主图/03-商品_细节___.webp'},
      {path: 'images/1.webp', archivePath: '详情页/01-商品_细节___.webp'},
      {path: 'images/3.webp', archivePath: '详情页/03-商品_细节___.webp'},
    ]);
  });
  it('重试时只取界面所显示的最新版本，不回退下载旧版本或缩略图', () => {
    const current = group('main_images');
    current.tasks.push({...current.tasks[2], id: 'retry-1', status: 'failed'});
    current.tasks[1].result_data = {thumbnail_url: 'https://cdn/thumbnail.png'};
    expect(detailImageDownloads([current])).toEqual([]);
    current.tasks[current.tasks.length - 1] = {...current.tasks.at(-1)!, status: 'completed',
      result_data: {workspace_path: 'images/retry.png'}};
    expect(detailImageDownloads([current])).toEqual([
      {path: 'images/retry.png', archivePath: '主图/01-商品_细节___.png'},
    ]);
  });
});

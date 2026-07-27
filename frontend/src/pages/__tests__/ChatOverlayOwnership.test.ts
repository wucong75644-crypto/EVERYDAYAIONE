import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const chatSource = readFileSync(resolve(process.cwd(), 'src/pages/Chat.tsx'), 'utf8');
const sidebarSource = readFileSync(
  resolve(process.cwd(), 'src/components/chat/layout/Sidebar.tsx'),
  'utf8',
);

describe('Chat 全局弹层挂载边界', () => {
  it('AI 记忆弹窗由 Chat 页面常驻挂载，不依赖可收起的 Sidebar', () => {
    expect(chatSource).toContain('<MemoryModal />');
    expect(sidebarSource).not.toContain('<MemoryModal />');
    expect(sidebarSource).not.toContain("import MemoryModal");
  });
});

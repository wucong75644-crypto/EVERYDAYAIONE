import { expect, it } from 'vitest';
import { groupChatImages, imagePlanText } from '../chatImageDisplay';
import { normalizeMessage } from '../../../../utils/messageUtils';
const image = (id: string, status = 'pending') => normalizeMessage({
  id, conversation_id: 'c1', role: 'assistant', status,
  content: [{ type: 'image', url: status === 'completed' ? `${id}.png` : null }],
  generation_params: { origin: 'chat_image', type: 'image', task_id: `task-${id}` },
});
it('groups six pending, completed and failed children in original order', () => {
  const messages = ['pending', 'completed', 'failed', 'pending', 'completed', 'pending']
    .map((status, i) => image(String(i), status));
  const groups = groupChatImages(messages);
  expect(groups).toHaveLength(1);
  expect(groups[0].map(message => message.id)).toEqual(['0', '1', '2', '3', '4', '5']);
  expect(groups[0][2].status).toBe('failed');
});
it('never combines images across user requests or ordinary replies', () => {
  const user = normalizeMessage({ id: 'user', conversation_id: 'c1', role: 'user', content: '再生成' });
  const reply = normalizeMessage({ id: 'reply', conversation_id: 'c1', role: 'assistant', content: '方案' });
  expect(groupChatImages([image('a'), image('b'), user, image('c'), reply, image('d')])
    .map(group => group.map(message => message.id))).toEqual([['a', 'b'], ['user'], ['c'], ['reply'], ['d']]);
});
it('keeps plans and removes old task receipt sections', () => {
  const plans = '选中的方案：\n1. 星空音乐家\n2. 几何极简主义';
  expect(imagePlanText(`${plans}\n\n现在我将依次提交这2张图片的生成任务。\n✅ 2张图片任务已全部提交成功！\n本次提交的任务：\n|任务ID|状态|\n|abc|排队中|\n说明：每张6积分`)).toBe(plans);
  expect(imagePlanText('✅ 3张图片任务已全部提交成功！\n本次提交的任务：')).toBe('');
  expect(imagePlanText('请检查参考图后重试。')).toBe('请检查参考图后重试。');
});

it('removes the comparison and receipt tail seen in older generated replies', () => {
  const plans = '方案1 | 星际探索\n方案4 | 甜品乐园';
  expect(imagePlanText(`${plans}\n\n这两个方案风格差异明显，一个偏向科技梦幻。\n现在开始生成这两张图片。\n已成功提交两张图片生成任务：`)).toBe(plans);
  expect(imagePlanText(`${plans}\n现在开始生成这两张图片，画布1:1。`)).toBe(plans);
});

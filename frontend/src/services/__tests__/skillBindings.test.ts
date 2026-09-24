import { beforeEach, expect, it, vi } from 'vitest';
import { request } from '../api';
import { addSkillBinding, getSkillBindings, removeSkillBinding } from '../skills';

vi.mock('../api', () => ({ request: vi.fn() }));
beforeEach(() => vi.mocked(request).mockResolvedValue({}));

it('writes only the explicit Skill identity and scopes every call to a conversation', async () => {
  await getSkillBindings('conv-1');
  expect(request).toHaveBeenLastCalledWith({ method: 'GET', url: '/skills/conversations/conv-1/bindings' });
  const summary = { skill_id: 'orders', revision: 'v1', body: 'not sent', scope: 'model' };
  await addSkillBinding('conv-1', summary);
  expect(request).toHaveBeenLastCalledWith({ method: 'POST', url: '/skills/conversations/conv-1/bindings',
    data: { skill_id: 'orders', revision: 'v1' } });
  await removeSkillBinding('conv-2', 'binding-1');
  expect(request).toHaveBeenLastCalledWith({ method: 'DELETE', url: '/skills/conversations/conv-2/bindings/binding-1' });
});

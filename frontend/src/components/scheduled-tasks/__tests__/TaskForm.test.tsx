import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { orgMembersService } from '../../../services/orgMembers';
import { TaskForm } from '../TaskForm';
import { scheduledTaskService } from '../../../services/scheduledTask';
import { wecomChatTargetsService } from '../../../services/wecomChatTargets';
import type { ParseNLResult } from '../../../types/scheduledTask';

const access = vi.hoisted(() => ({ others: true }));
vi.mock('../../../hooks/usePermission', () => ({ usePermission: () => access.others }));
vi.mock('../../../stores/useAuthStore', () => ({ useAuthStore: (select: (s: unknown) => unknown) => select({ user: { id: 'u1' } }) }));
vi.mock('../../../services/scheduledTask', () => ({ scheduledTaskService: { parseNL: vi.fn(), proposeChange: vi.fn() } }));
vi.mock('../../../services/orgMembers', () => ({ orgMembersService: {
  getMyMemberInfo: vi.fn().mockResolvedValue({}),
  listWecomCollected: vi.fn().mockResolvedValue([{ user_id: 'u2', wecom_userid: 'wx2', nickname: '小王' }]),
} }));
vi.mock('../../../services/wecomChatTargets', () => ({ wecomChatTargetsService: {
  listGroups: vi.fn().mockResolvedValue([{ id: 'g1', chatid: 'chat1', chat_name: '销售群' }]),
} }));
const complete = { name: 'A店日报', prompt: '查询A店付款订单', schedule_type: 'daily', time_str: '08:00', missing_fields: [], recipient: '' };
async function parse(result: Partial<ParseNLResult>, text = '新的创建请求') {
  vi.mocked(scheduledTaskService.parseNL).mockResolvedValue(result as ParseNLResult);
  fireEvent.change(screen.getByPlaceholderText(/今晚10点推/), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: '解析' }));
  await waitFor(() => expect(screen.getByPlaceholderText(/今晚10点推/)).toHaveValue('')); 
}

describe('TaskForm request contract', () => {
  beforeEach(() => {
    vi.clearAllMocks(); access.others = true;
    vi.mocked(orgMembersService.getMyMemberInfo).mockResolvedValue({} as never);
    vi.mocked(wecomChatTargetsService.listGroups).mockResolvedValue([{ id: 'g1', chatid: 'chat1', chat_name: '销售群' }] as never);
    vi.mocked(scheduledTaskService.proposeChange).mockResolvedValue({ id: 'cs1' } as never);
  });
  it('replaces a new draft and blocks submission when the second request lacks execution content', async () => {
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await parse(complete as ParseNLResult);
    await parse({ ...complete, name: 'B店日报', prompt: '', time_str: '10:00', missing_fields: ['prompt'] } as ParseNLResult);
    expect(screen.queryByDisplayValue('查询A店付款订单')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    expect(scheduledTaskService.proposeChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByPlaceholderText(/查询昨日各店铺/), { target: { value: '查询B店付款订单' } });
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(scheduledTaskService.proposeChange).toHaveBeenCalledWith(expect.objectContaining({ definition: expect.objectContaining({ name: 'B店日报', prompt: '查询B店付款订单', time_str: '10:00' }) })));
  });
  it('does not inherit frequency, weekly days or monthly date from a preceding parse', async () => {
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await parse({ ...complete, schedule_type: 'weekly', weekdays: [1, 3] } as ParseNLResult);
    await parse({ name: 'B', prompt: '查询B店', time_str: '10:00', missing_fields: ['schedule_type'] });
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    expect(screen.getByText('请选择执行频率')).toBeVisible();
    expect(scheduledTaskService.proposeChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '每周' }));
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    expect(screen.getByText('请至少选择一天')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '每月' }));
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    expect(screen.getByText('请选择每月执行日期')).toBeVisible();
  });
  it('submits the authorized exact group requested in natural language', async () => {
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await screen.findByRole('option', { name: '销售群' });
    await parse({ ...complete, recipient: '销售群' } as ParseNLResult);
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(scheduledTaskService.proposeChange).toHaveBeenCalledWith(expect.objectContaining({ definition: expect.objectContaining({ push_target: { type: 'wecom_group', chatid: 'chat1', chat_name: '销售群' } }) })));
  });
  it.each([true, false])('requires explicit correction for an unavailable recipient (permission=%s)', async (permission) => {
    access.others = permission;
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await parse({ ...complete, recipient: permission ? '不存在的群' : '销售群' } as ParseNLResult);
    expect(screen.getByRole('alert')).toHaveTextContent('选择前不会提交');
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    expect(scheduledTaskService.proposeChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('推送给我自己'));
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(scheduledTaskService.proposeChange).toHaveBeenCalledWith(expect.objectContaining({ definition: expect.objectContaining({ push_target: { type: 'web', user_id: 'u1' } }) })));
  });
  it('does not choose arbitrarily between identically named groups', async () => {
    vi.mocked(wecomChatTargetsService.listGroups).mockResolvedValue([
      { id: 'g1', chatid: 'chat1', chat_name: '销售群' },
      { id: 'g2', chatid: 'chat2', chat_name: '销售群' },
    ] as never);
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await screen.findAllByRole('option', { name: '销售群' });
    await parse({ ...complete, recipient: '销售群' } as ParseNLResult);
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    expect(scheduledTaskService.proposeChange).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('销售群');
  });

  it('cannot submit the preceding draft while a new parse is outstanding', async () => {
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await parse(complete as ParseNLResult);
    let finish!: (result: ParseNLResult) => void;
    vi.mocked(scheduledTaskService.parseNL).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    fireEvent.change(screen.getByPlaceholderText(/今晚10点推/), { target: { value: '查询B店' } });
    fireEvent.click(screen.getByRole('button', { name: '解析' }));
    expect(screen.getByRole('button', { name: '创建任务' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    expect(scheduledTaskService.proposeChange).not.toHaveBeenCalled();
    finish({ ...complete, name: 'B店', prompt: '查询B店' } as ParseNLResult);
    await screen.findByDisplayValue('查询B店');
  });

  it.each(['web', 'wecom_user'])('lets a mapped user explicitly choose %s', async (channel) => {
    vi.mocked(orgMembersService.getMyMemberInfo).mockResolvedValue({ wecom_userid: 'wx1' } as never);
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('option', { name: '企业微信个人通知' })).toBeEnabled());
    expect(screen.getByRole('combobox', { name: '通知渠道' })).toHaveValue('web');
    await parse(complete as ParseNLResult);
    fireEvent.change(screen.getByRole('combobox', { name: '通知渠道' }), { target: { value: channel } });
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(scheduledTaskService.proposeChange).toHaveBeenCalledWith(expect.objectContaining({ definition: expect.objectContaining({
      push_target: channel === 'web' ? { type: 'web', user_id: 'u1' } : { type: 'wecom_user', wecom_userid: 'wx1' },
    }) })));
  });

  it('keeps an existing personal WeCom target when editing', async () => {
    vi.mocked(orgMembersService.getMyMemberInfo).mockResolvedValue({ wecom_userid: 'wx1' } as never);
    render(<TaskForm task={{ id: 't1', name: '旧日报', prompt: '查询订单', schedule_type: 'daily',
      cron_expr: '0 9 * * *', push_target: { type: 'wecom_user', wecom_userid: 'wx1' },
    } as never} onClose={vi.fn()} onProposed={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('option', { name: '企业微信个人通知' })).toBeEnabled());
    expect(screen.getByRole('combobox', { name: '通知渠道' })).toHaveValue('wecom_user');
  });
  it('does not allow an unbound personal WeCom channel', async () => {
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await screen.findByRole('option', { name: '销售群' });
    expect(screen.getByRole('option', { name: '企业微信个人通知（未绑定）' })).toBeDisabled();
  });

});

describe('TaskForm structured AI fill', () => {
  beforeEach(() => {
    vi.clearAllMocks(); access.others = false;
    vi.mocked(orgMembersService.getMyMemberInfo).mockResolvedValue({ wecom_userid: 'wx1' } as never);
    vi.mocked(scheduledTaskService.proposeChange).mockResolvedValue({ id: 'cs1' } as never);
  });
  it('honors the requested personal WeCom channel without allowing other recipients', async () => {
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('option', { name: '企业微信个人通知' })).toBeEnabled());
    await parse({ ...complete, recipient: '我（企微）' } as ParseNLResult);
    expect(screen.getByRole('combobox', { name: '通知渠道' })).toHaveValue('wecom_user');
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(scheduledTaskService.proposeChange).toHaveBeenCalledWith(expect.objectContaining({
      definition: expect.objectContaining({ push_target: { type: 'wecom_user', wecom_userid: 'wx1' } }),
    })));
  });
  it('retains input and edited business fields when structured extraction fails', async () => {
    render(<TaskForm task={null} onClose={vi.fn()} onProposed={vi.fn()} />);
    await parse(complete as ParseNLResult);
    vi.mocked(scheduledTaskService.parseNL).mockRejectedValue(new Error('invalid structured fields'));
    fireEvent.change(screen.getByPlaceholderText(/今晚10点推/), { target: { value: '新的学习建议' } });
    fireEvent.click(screen.getByRole('button', { name: '解析' }));
    await screen.findByText('解析失败，请重试或手动填写任务。');
    expect(screen.getByPlaceholderText(/今晚10点推/)).toHaveValue('新的学习建议');
    expect(screen.getByPlaceholderText(/查询昨日各店铺/)).toHaveValue(complete.prompt);
  });
  it('keeps the other fields when manually editing an existing task time', async () => {
    const { container } = render(<TaskForm task={{ id: 't1', name: '原任务', prompt: '读取A店订单，排除退款并按平台汇总',
      schedule_type: 'weekly', cron_expr: '0 8 * * 1,5', weekdays: [1, 5], timezone: 'Asia/Shanghai',
      push_target: { type: 'web', user_id: 'u1' },
    } as never} onClose={vi.fn()} onProposed={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('option', { name: '企业微信个人通知' })).toBeEnabled());
    fireEvent.change(container.querySelector('input[type="time"]')!, { target: { value: '10:00' } });
    expect(scheduledTaskService.parseNL).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText(/查询昨日各店铺/)).toHaveValue('读取A店订单，排除退款并按平台汇总');
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
    await waitFor(() => expect(scheduledTaskService.proposeChange).toHaveBeenCalledWith(expect.objectContaining({
      task_id: 't1', operation: 'update', definition: expect.objectContaining({
        time_str: '10:00', weekdays: [1, 5], schedule_type: 'weekly', name: '原任务', prompt: '读取A店订单，排除退款并按平台汇总',
      }),
    })));
  });
});

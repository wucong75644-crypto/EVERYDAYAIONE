import { describe, expect, it } from 'vitest';

import { ApiRequestError, toApiRequestError } from '../api';


describe('toApiRequestError', () => {
  it('extracts backend business error message', () => {
    const result = toApiRequestError({
      isAxiosError: true,
      response: {
        status: 402,
        data: {
          error: {
            code: 'INSUFFICIENT_CREDITS',
            message: '积分不足，需要 20 积分，当前余额 5 积分',
            details: { required: 20, current: 5 },
          },
        },
      },
    });

    expect(result).toBeInstanceOf(ApiRequestError);
    expect(result.code).toBe('INSUFFICIENT_CREDITS');
    expect(result.message).toBe('积分不足，需要 20 积分，当前余额 5 积分');
    expect(result.status).toBe(402);
  });
});

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
    expect(result.transport).toBe('http');
  });

  it('classifies axios timeout separately from network failure', () => {
    const result = toApiRequestError({
      isAxiosError: true,
      code: 'ECONNABORTED',
      message: 'timeout of 60000ms exceeded',
    });

    expect(result.code).toBe('REQUEST_TIMEOUT');
    expect(result.transport).toBe('timeout');
  });

  it('classifies axios errors without a response as network failures', () => {
    const result = toApiRequestError({
      isAxiosError: true,
      code: 'ERR_NETWORK',
      message: 'Network Error',
    });

    expect(result.code).toBe('NETWORK_ERROR');
    expect(result.transport).toBe('network');
  });

  it('converts backend retry_after seconds to milliseconds', () => {
    const error = new ApiRequestError(
      'IDEMPOTENCY_REQUEST_IN_PROGRESS', '请求处理中', 409,
      { retry_after: 1.5 },
    );

    expect(error.retryAfterMs).toBe(1500);
  });
});

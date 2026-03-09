/**
 * smartModel.ts 单元测试
 */

import { describe, expect, it } from 'vitest';

import { SMART_MODEL_ID, isSmartModel } from '../smartModel';

describe('smartModel', () => {
  it('SMART_MODEL_ID === "auto"', () => {
    expect(SMART_MODEL_ID).toBe('auto');
  });

  it('isSmartModel("auto") → true', () => {
    expect(isSmartModel('auto')).toBe(true);
  });

  it('isSmartModel("gemini-3-pro") → false', () => {
    expect(isSmartModel('gemini-3-pro')).toBe(false);
  });

  it('isSmartModel("") → false', () => {
    expect(isSmartModel('')).toBe(false);
  });
});

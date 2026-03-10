/**
 * useModelSelection Hook 单元测试
 *
 * 覆盖：模型选择、冲突检测、发送按钮状态、积分估算、可用模型过滤
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useModelSelection } from '../useModelSelection';
import { ALL_MODELS } from '../../constants/models';

// ============================================================
// Mocks
// ============================================================

vi.mock('react-hot-toast', () => ({
  default: vi.fn(),
}));

const mockFetchModels = vi.fn();
const mockFetchSubscriptions = vi.fn();
let mockSubscribedModelIds: string[] = [];

vi.mock('../../stores/useSubscriptionStore', () => ({
  useSubscriptionStore: (selector?: (s: unknown) => unknown) => {
    const state = {
      subscribedModelIds: mockSubscribedModelIds,
      fetchModels: mockFetchModels,
      fetchSubscriptions: mockFetchSubscriptions,
    };
    if (selector) return selector(state);
    return state;
  },
}));

let mockIsAuthenticated = false;
vi.mock('../../stores/useAuthStore', () => ({
  useAuthStore: (selector?: (s: unknown) => unknown) => {
    const state = { isAuthenticated: mockIsAuthenticated };
    if (selector) return selector(state);
    return state;
  },
}));

// ============================================================
// 辅助
// ============================================================

const SMART_MODEL = ALL_MODELS[0]; // id='auto'
const CHAT_MODEL = ALL_MODELS.find((m) => m.id === 'gemini-3-flash')!;
const IMAGE_MODEL = ALL_MODELS.find((m) => m.id === 'google/nano-banana')!;
const EDIT_MODEL = ALL_MODELS.find((m) => m.id === 'google/nano-banana-edit')!;
const VIDEO_MODEL = ALL_MODELS.find((m) => m.id === 'sora-2-text-to-video')!;
const PRO_IMAGE_MODEL = ALL_MODELS.find((m) => m.id === 'nano-banana-pro')!;

function defaultParams() {
  return {
    hasImage: false,
    hasQuotedImage: false,
    conversationId: null as string | null,
    conversationModelId: null as string | null,
    onAutoSaveModel: vi.fn(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSubscribedModelIds = [];
  mockIsAuthenticated = false;
});

// ============================================================
// 初始状态
// ============================================================

describe('初始状态', () => {
  it('默认选中智能模型（auto）', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    expect(result.current.selectedModel.id).toBe('auto');
  });

  it('初始无冲突', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    expect(result.current.modelConflict).toBeNull();
  });

  it('初始 userExplicitChoice 为 false', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    expect(result.current.userExplicitChoice).toBe(false);
  });
});

// ============================================================
// getModelSelectorLockState
// ============================================================

describe('getModelSelectorLockState', () => {
  it('上传中时锁定', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    const state = result.current.getModelSelectorLockState(true);
    expect(state.locked).toBe(true);
    expect(state.tooltip).toContain('上传中');
  });

  it('非上传时不锁定', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    const state = result.current.getModelSelectorLockState(false);
    expect(state.locked).toBe(false);
  });
});

// ============================================================
// getSendButtonState
// ============================================================

describe('getSendButtonState', () => {
  it('上传中禁用', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    const state = result.current.getSendButtonState(false, true, true);
    expect(state.disabled).toBe(true);
    expect(state.tooltip).toContain('上传中');
  });

  it('提交中禁用', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    const state = result.current.getSendButtonState(true, false, true);
    expect(state.disabled).toBe(true);
    expect(state.tooltip).toContain('发送中');
  });

  it('无内容禁用', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    const state = result.current.getSendButtonState(false, false, false);
    expect(state.disabled).toBe(true);
    expect(state.tooltip).toContain('请输入');
  });

  it('正常状态可发送', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));
    const state = result.current.getSendButtonState(false, false, true);
    expect(state.disabled).toBe(false);
    expect(state.tooltip).toBe('发送');
  });

  it('硬性冲突时禁用发送', () => {
    // 选中纯编辑模型 + 无图片 → requires_image 冲突
    const { result } = renderHook(() => useModelSelection(defaultParams()));

    act(() => {
      result.current.handleUserSelectModel(EDIT_MODEL);
    });

    const state = result.current.getSendButtonState(false, false, true);
    expect(state.disabled).toBe(true);
    expect(state.tooltip).toContain('上传图片');
  });
});

// ============================================================
// handleUserSelectModel
// ============================================================

describe('handleUserSelectModel', () => {
  it('切换模型并设置 userExplicitChoice', () => {
    const onAutoSave = vi.fn();
    const { result } = renderHook(() =>
      useModelSelection({ ...defaultParams(), onAutoSaveModel: onAutoSave }),
    );

    act(() => {
      result.current.handleUserSelectModel(CHAT_MODEL);
    });

    expect(result.current.selectedModel.id).toBe('gemini-3-flash');
    expect(result.current.userExplicitChoice).toBe(true);
  });

  it('触发 onAutoSaveModel 回调', () => {
    const onAutoSave = vi.fn();
    const { result } = renderHook(() =>
      useModelSelection({ ...defaultParams(), onAutoSaveModel: onAutoSave }),
    );

    act(() => {
      result.current.handleUserSelectModel(CHAT_MODEL);
    });

    expect(onAutoSave).toHaveBeenCalledWith('gemini-3-flash');
  });

  it('触发高亮动画', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));

    act(() => {
      result.current.handleUserSelectModel(CHAT_MODEL);
    });

    expect(result.current.modelJustSwitched).toBe(true);
  });
});

// ============================================================
// modelConflict（冲突检测）
// ============================================================

describe('modelConflict', () => {
  it('智能模型无冲突（即使有图片）', () => {
    const { result } = renderHook(() =>
      useModelSelection({ ...defaultParams(), hasImage: true }),
    );
    expect(result.current.selectedModel.id).toBe('auto');
    expect(result.current.modelConflict).toBeNull();
  });

  it('文生图模型 + 有图片 → critical 冲突', () => {
    const { result } = renderHook(() =>
      useModelSelection({ ...defaultParams(), hasImage: true }),
    );

    act(() => {
      result.current.handleUserSelectModel(IMAGE_MODEL);
    });

    expect(result.current.modelConflict?.severity).toBe('critical');
    expect(result.current.modelConflict?.type).toBe('no_image_support');
  });

  it('编辑模型 + 无图片 → requires_image 冲突', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));

    act(() => {
      result.current.handleUserSelectModel(EDIT_MODEL);
    });

    expect(result.current.modelConflict?.severity).toBe('critical');
    expect(result.current.modelConflict?.type).toBe('requires_image');
  });

  it('聊天模型 + 有图片 → 无冲突', () => {
    const { result } = renderHook(() =>
      useModelSelection({ ...defaultParams(), hasImage: true }),
    );

    act(() => {
      result.current.handleUserSelectModel(CHAT_MODEL);
    });

    expect(result.current.modelConflict).toBeNull();
  });
});

// ============================================================
// getEstimatedCredits
// ============================================================

describe('getEstimatedCredits', () => {
  it('聊天模型返回按量计费', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));

    act(() => {
      result.current.handleUserSelectModel(CHAT_MODEL);
    });

    expect(result.current.getEstimatedCredits('1K')).toBe('按使用量计费');
  });

  it('视频模型返回积分/10秒', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));

    act(() => {
      result.current.handleUserSelectModel(VIDEO_MODEL);
    });

    expect(result.current.getEstimatedCredits('1K')).toContain('积分/10秒');
  });

  it('有图片时返回编辑积分', () => {
    const { result } = renderHook(() =>
      useModelSelection({ ...defaultParams(), hasImage: true }),
    );

    act(() => {
      result.current.handleUserSelectModel(PRO_IMAGE_MODEL);
    });

    expect(result.current.getEstimatedCredits('1K')).toContain('图像编辑');
  });

  it('文生图模型按分辨率返回积分', () => {
    const { result } = renderHook(() => useModelSelection(defaultParams()));

    act(() => {
      result.current.handleUserSelectModel(PRO_IMAGE_MODEL);
    });

    const credits4K = result.current.getEstimatedCredits('4K');
    expect(credits4K).toContain('24');
  });
});

// ============================================================
// availableModels（订阅过滤）
// ============================================================

describe('availableModels', () => {
  it('无订阅时只有智能模型', () => {
    mockSubscribedModelIds = [];
    const { result } = renderHook(() => useModelSelection(defaultParams()));

    // auto 始终可用
    expect(result.current.availableModels.some((m) => m.id === 'auto')).toBe(true);
    // 未订阅模型不在列表
    expect(result.current.availableModels.some((m) => m.id === 'gemini-3-flash')).toBe(false);
  });

  it('已订阅模型出现在可用列表', () => {
    mockSubscribedModelIds = ['gemini-3-flash', 'deepseek-v3.2'];
    const { result } = renderHook(() => useModelSelection(defaultParams()));

    expect(result.current.availableModels.some((m) => m.id === 'auto')).toBe(true);
    expect(result.current.availableModels.some((m) => m.id === 'gemini-3-flash')).toBe(true);
    expect(result.current.availableModels.some((m) => m.id === 'deepseek-v3.2')).toBe(true);
    expect(result.current.availableModels.some((m) => m.id === 'gemini-3-pro')).toBe(false);
  });
});

// ============================================================
// 数据加载
// ============================================================

describe('数据加载', () => {
  it('初始化时调用 fetchModels', () => {
    renderHook(() => useModelSelection(defaultParams()));
    expect(mockFetchModels).toHaveBeenCalled();
  });

  it('已登录时调用 fetchSubscriptions', () => {
    mockIsAuthenticated = true;
    renderHook(() => useModelSelection(defaultParams()));
    expect(mockFetchSubscriptions).toHaveBeenCalled();
  });

  it('未登录时不调用 fetchSubscriptions', () => {
    mockIsAuthenticated = false;
    renderHook(() => useModelSelection(defaultParams()));
    expect(mockFetchSubscriptions).not.toHaveBeenCalled();
  });
});

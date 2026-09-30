import type { ComponentProps } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ALL_MODELS } from '../../../../constants/models';
import AdvancedSettingsMenu from '../AdvancedSettingsMenu';

function props(modelId: string): ComponentProps<typeof AdvancedSettingsMenu> {
  return {
    selectedModel: ALL_MODELS.find((model) => model.id === modelId)!,
    aspectRatio: '1:1', onAspectRatioChange: vi.fn(),
    resolution: '4K', onResolutionChange: vi.fn(),
    outputFormat: 'png', onOutputFormatChange: vi.fn(),
    numImages: 1, onNumImagesChange: vi.fn(),
    videoFrames: '10', onVideoFramesChange: vi.fn(),
    videoAspectRatio: 'landscape', onVideoAspectRatioChange: vi.fn(),
    removeWatermark: false, onRemoveWatermarkChange: vi.fn(),
    onSave: vi.fn(), onReset: vi.fn(), onClose: vi.fn(),
  };
}

describe('GPT Image 2.5 settings', () => {
  it.each(['text-to-image', 'image-to-image'])('keeps 4K for square and auto in %s', (mode) => {
    const p = props(`gpt-image-2-5-flare-${mode}`);
    const view = render(<AdvancedSettingsMenu {...p} />);
    expect(screen.getByRole('button', { name: /4K.*16积分/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Auto (自动)' }));
    expect(p.onAspectRatioChange).toHaveBeenCalledWith('auto');
    expect(p.onResolutionChange).not.toHaveBeenCalled();
    view.rerender(<AdvancedSettingsMenu {...p} aspectRatio="auto" />);
    expect(screen.getByRole('button', { name: /4K.*16积分/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '4:5 (短竖)' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '5:4 (短横)' })).not.toBeInTheDocument();
  });

  it('uses the new default model controls in auto image mode', () => {
    render(<AdvancedSettingsMenu {...props('auto')} effectiveModelType="image" />);
    expect(screen.getByRole('button', { name: /4K.*16积分/ })).toBeEnabled();
  });

  it('preserves the existing GPT Image 2 restrictions', () => {
    const p = props('gpt-image-2-text-to-image');
    render(<AdvancedSettingsMenu {...p} />);
    expect(screen.getByRole('button', { name: /4K.*16积分/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Auto (自动)' }));
    expect(p.onResolutionChange).toHaveBeenCalledWith('1K');
  });
});

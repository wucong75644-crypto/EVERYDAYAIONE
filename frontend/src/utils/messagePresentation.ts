import type { CSSProperties } from 'react';
import contract from '../../../backend/config/message_presentation.json';

export const presentationColors = contract.colors;
export type PresentationColor = keyof typeof presentationColors;
export interface CellStyle { color?: PresentationColor; bold?: boolean }
export type TableCellStyles = Record<string, CellStyle>[];

export function isPresentationColor(value: unknown): value is PresentationColor {
  return typeof value === 'string' && Object.hasOwn(presentationColors, value);
}

/** 这里只输出可信目录中的样式值，绝不透传模型提供的 CSS。 */
export function presentationColorProps(value: unknown) {
  if (!isPresentationColor(value)) return {};
  const { light, dark } = presentationColors[value];
  return {
    'data-color': value,
    className: 'message-color',
    style: { '--message-color-light': light, '--message-color-dark': dark } as CSSProperties,
  };
}

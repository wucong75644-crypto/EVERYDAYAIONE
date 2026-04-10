/**
 * primitives/ 统一出口
 *
 * 所有 Radix 底座封装从这里 re-export，
 * 业务组件 import 时只写 `from 'components/primitives'`。
 */

export { Dialog, DialogFooter, DialogClose } from './Dialog';
export type { DialogSize, DialogBackdrop } from './Dialog';

export {
  DropdownMenu,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from './DropdownMenu';

export { Popover, PopoverClose } from './Popover';

export { Tooltip } from './Tooltip';

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import userEvent from '@testing-library/user-event';
import { CanvasContextMenu, CanvasMenuItem } from '../CanvasContextMenu';
function MenuFixture({ onReturn = vi.fn(), onChoose = vi.fn() }: { onReturn?: () => void; onChoose?: () => void }) {
  const [open,setOpen]=useState(true);
  return <><input aria-label="外部搜索" />{open && <CanvasContextMenu position={{x:1000,y:700}} onClose={()=>setOpen(false)} onReturnFocus={onReturn}>
    <CanvasMenuItem onSelect={onChoose}>第一项</CanvasMenuItem>
    <CanvasMenuItem disabled onSelect={onChoose}>不可用</CanvasMenuItem>
    <CanvasMenuItem onSelect={onChoose}>最后一项</CanvasMenuItem>
  </CanvasContextMenu>}</>;
}
describe('canvas context menu',()=>{
  it('focuses the first enabled item, wraps arrows and skips unavailable items',async()=>{
    render(<MenuFixture/>);const first=await screen.findByRole('menuitem',{name:'第一项'});
    await waitFor(()=>expect(first).toHaveFocus());
    fireEvent.keyDown(first,{key:'ArrowDown'});await waitFor(()=>expect(screen.getByRole('menuitem',{name:'最后一项'})).toHaveFocus());
    fireEvent.keyDown(document.activeElement!,{key:'ArrowDown'});await waitFor(()=>expect(first).toHaveFocus());
    fireEvent.keyDown(first,{key:'ArrowUp'});await waitFor(()=>expect(screen.getByRole('menuitem',{name:'最后一项'})).toHaveFocus());
    fireEvent.keyDown(document.activeElement!,{key:'Home'});await waitFor(()=>expect(first).toHaveFocus());
    fireEvent.keyDown(first,{key:'End'});await waitFor(()=>expect(screen.getByRole('menuitem',{name:'最后一项'})).toHaveFocus());
  });
  it('activates one item using Enter and closes the menu',async()=>{
    const choose=vi.fn(),returned=vi.fn();render(<MenuFixture onChoose={choose} onReturn={returned}/>);
    const first=await screen.findByRole('menuitem',{name:'第一项'});await waitFor(()=>expect(first).toHaveFocus());
    fireEvent.keyDown(first,{key:'Enter'});
    await waitFor(()=>expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    expect(choose).toHaveBeenCalledTimes(1);await waitFor(()=>expect(returned).toHaveBeenCalledTimes(1));
  });
  it.each(['Escape','Tab'])('dismisses using %s and returns focus without choosing an action',async key=>{
    const choose=vi.fn(),returned=vi.fn();render(<MenuFixture onChoose={choose} onReturn={returned}/>);
    const first=await screen.findByRole('menuitem',{name:'第一项'});await waitFor(()=>expect(first).toHaveFocus());
    fireEvent.keyDown(first,{key});await waitFor(()=>expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    await waitFor(()=>expect(returned).toHaveBeenCalledTimes(1));expect(choose).not.toHaveBeenCalled();
  });
  it('preserves focus when the user moves directly to an outside input',async()=>{
    const returned=vi.fn();render(<MenuFixture onReturn={returned}/>);
    await waitFor(()=>expect(screen.getByRole('menuitem',{name:'第一项'})).toHaveFocus());
    await userEvent.setup().click(screen.getByLabelText('外部搜索'));
    await waitFor(()=>expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    expect(returned).not.toHaveBeenCalled();
    expect(screen.getByLabelText('外部搜索')).toHaveFocus();
  });
});

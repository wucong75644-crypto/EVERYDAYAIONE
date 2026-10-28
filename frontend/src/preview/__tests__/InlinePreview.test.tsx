import {fireEvent,render,screen} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import PreviewFrame from '../PreviewFrame';
import {InlinePreviewContext} from '../InlinePreviewContext';
it('embeds a document in its parent without a portal, scroll lock, or global Escape listener',()=>{
 const onClose=vi.fn(),onOpen=vi.fn();document.body.style.overflow='auto';const view=render(<InlinePreviewContext.Provider value={{onOpen}}><PreviewFrame item={{filename:'培训文档.txt'}} onClose={onClose}><p>原始文档预览</p></PreviewFrame></InlinePreviewContext.Provider>);const preview=screen.getByRole('region',{name:'预览 培训文档.txt'});expect(view.container.contains(preview)).toBe(true);expect(document.body.style.overflow).toBe('auto');expect(screen.getByText('原始文档预览')).toBeVisible();fireEvent.keyDown(window,{key:'Escape'});expect(onClose).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'放大预览 培训文档.txt'}));expect(onOpen).toHaveBeenCalledOnce();view.unmount();expect(document.body.style.overflow).toBe('auto');document.body.style.overflow='';
});

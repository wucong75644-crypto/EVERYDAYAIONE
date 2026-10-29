import {fireEvent,render,screen} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {NodeUploadQueue} from '../NodeUploadQueue';
import type {KnowledgeObject} from '../../../services/knowledge';
vi.mock('../KnowledgeFiles',()=>({KnowledgeFiles:({entry,onFilesChanged,onClose}:{entry:{file:File};onFilesChanged:()=>void;onClose:()=>void})=><div>{entry.file.name}<button onClick={()=>{onFilesChanged();onClose();}}>上传已确认</button><button onClick={onClose}>取消</button></div>}));
const object={id:'node'} as KnowledgeObject;
it('advances only after the current file receipt is confirmed and closes after the last',()=>{
 const onChanged=vi.fn(),onClose=vi.fn();render(<NodeUploadQueue object={object} nodes={[]} files={[new File(['a'],'first.png'),new File(['b'],'second.mp4')]} onChanged={onChanged} onClose={onClose}/>);
 expect(screen.getByText('first.png')).toBeVisible();fireEvent.click(screen.getByText('上传已确认'));expect(screen.getByText('second.mp4')).toBeVisible();expect(onClose).not.toHaveBeenCalled();fireEvent.click(screen.getByText('上传已确认'));expect(onClose).toHaveBeenCalledTimes(1);expect(onChanged).toHaveBeenCalledTimes(2);
});
it('cancelling an unconfirmed file stops the queue instead of uploading later files',()=>{
 const onClose=vi.fn(),onChanged=vi.fn();render(<NodeUploadQueue object={object} nodes={[]} files={[new File(['a'],'first.png'),new File(['b'],'second.mp4')]} onChanged={onChanged} onClose={onClose}/>);fireEvent.click(screen.getByText('取消'));expect(onClose).toHaveBeenCalledOnce();expect(onChanged).not.toHaveBeenCalled();expect(screen.queryByText('second.mp4')).toBeNull();
});

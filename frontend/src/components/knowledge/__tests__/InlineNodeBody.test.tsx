import {act,fireEvent,render,screen} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import {InlineNodeBody} from '../InlineNodeBody';
import type {KnowledgeNode,KnowledgeObject} from '../../../services/knowledge';
vi.mock('../../../services/knowledge',()=>({knowledgeApi:{object:vi.fn()}}));
const node={id:'n',node_id:'n',container_id:'team',parent_id:null,title:'售后方法',kind:'topic',revision:1,layout_revision:1,x:0,y:0} as KnowledgeNode;
const object=(body:string)=>({...node,body,can_write:true}) as KnowledgeObject;
function setup(body:string){const onChange=vi.fn(),onSave=vi.fn();const props={node,editing:object(body),pending:false,unknown:false,error:'',onEdit:vi.fn(),onChange,onSave,onCheck:vi.fn(),onCancel:vi.fn(),onSize:vi.fn()};const view=render(<InlineNodeBody {...props}/>);return{...view,props,onChange,onSave,input:screen.getByRole('textbox') as HTMLTextAreaElement};}
it('keeps the complete long draft in a sizing mirror instead of shrinking to the textarea default rows',()=>{
 const body='你说'.repeat(150)+'\n最后一行';const {input,container}=setup(body);expect(input.value).toBe(body);expect(container.querySelector('.kc-body-measure')).toHaveTextContent(body.replace('\n',' '));expect(input.parentElement).toBe(container.querySelector('.kc-body-edit-surface'));expect(input.selectionStart).toBe(body.length);expect(input.selectionEnd).toBe(body.length);expect(input).toHaveFocus();
});
it('tracks wrapping content without resetting the caret on each edit',()=>{
 const {input,props,rerender,container,onChange}=setup('原文');input.setSelectionRange(1,1);fireEvent.change(input,{target:{value:'原新增文'}});expect(onChange).toHaveBeenCalledWith('原新增文');rerender(<InlineNodeBody {...props} editing={object('原新增文')}/>);expect(container.querySelector('.kc-body-measure')).toHaveTextContent('原新增文');expect(input.selectionStart).toBe(4);act(()=>input.setSelectionRange(1,1));rerender(<InlineNodeBody {...props} editing={object('原新增文')} pending/>);expect(input.selectionStart).toBe(1);
});
it('preserves an existing title prefix without inserting a duplicate heading into the edit surface',()=>{
 const {input,container,onChange}=setup('售后方法\n\n完整正文');expect(input.value).toBe('完整正文');expect(container.querySelector('.kc-body-measure')).toHaveTextContent('完整正文');fireEvent.change(input,{target:{value:'完整正文补充'}});expect(onChange).toHaveBeenCalledWith('售后方法\n\n完整正文补充');
});
it('retains the Enter save and Shift+Enter newline contract',()=>{const {input,onSave}=setup('完整正文');fireEvent.keyDown(input,{key:'Enter',shiftKey:true});expect(onSave).not.toHaveBeenCalled();fireEvent.keyDown(input,{key:'Enter'});expect(onSave).toHaveBeenCalledOnce();});

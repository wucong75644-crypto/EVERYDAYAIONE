import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {DirectoryPanel} from '../DirectoryPanel';
const derived=vi.hoisted(()=>({data:null as null|{sections:{id:string;title:string;text:string;source_refs:string[]}[];status:string},error:''}));
vi.mock('../useDerivedDirectory',()=>({useDerivedDirectory:()=>derived}));
const object={id:'n',container_id:'c',parent_id:null,kind:'topic' as const,title:'订单导出',revision:1,body:'记录类型：待验证历史方法\n\n## 导出步骤\n\n先筛选，再导出。',can_write:true};
const props={object,path:['历史资料','订单导出'],style:{left:100,top:200},zoom:1,inputMode:false,text:'',pending:false,error:'',unknown:false,onText:vi.fn(),onInputMode:vi.fn(),onSubmit:vi.fn(),onCheck:vi.fn(),onClose:vi.fn()};
afterEach(()=>{cleanup();vi.clearAllMocks();derived.data=null;});
it('opens a large readonly document below the supplied node coordinates',()=>{
 render(<DirectoryPanel {...props}/>);const panel=screen.getByRole('dialog',{name:'目录资料'});
 expect(panel).toHaveStyle({left:'100px',top:'200px',width:'860px',height:'640px'});
 expect(screen.getByRole('heading',{name:'导出步骤'})).toBeVisible();expect(screen.getByLabelText('节点路径')).toHaveTextContent('历史资料 › 订单导出');
 expect(within(panel).queryByRole('textbox')).toBeNull();expect(panel).toHaveAttribute('aria-modal','false');
 fireEvent.click(screen.getByRole('button',{name:'补充 / 修改'}));expect(props.onInputMode).toHaveBeenCalledWith(true);
 fireEvent.click(screen.getByRole('button',{name:'关闭目录资料'}));expect(props.onClose).toHaveBeenCalledOnce();
});
it('submits a separate instruction without making the document editable',()=>{
 render(<DirectoryPanel {...props} inputMode text="把时间改成昨天"/>);
 const input=screen.getByRole('textbox',{name:'补充 / 修改内容'});expect(input).toHaveFocus();expect(input).toHaveValue('把时间改成昨天');
 fireEvent.change(input,{target:{value:'补充一步：核对订单'}});expect(props.onText).toHaveBeenCalledWith('补充一步：核对订单');
 fireEvent.click(screen.getByRole('button',{name:'提交给 AI 整理'}));expect(props.onSubmit).toHaveBeenCalledOnce();
});
it('keeps saved body primary and generated sections available without duplicate editing',()=>{
 derived.data={status:'ready',sections:[{id:'s',title:'节点与资料状态',text:'历史资料，需要核对。',source_refs:[]}]};
 render(<DirectoryPanel {...props}/>);expect(screen.getByRole('heading',{name:'导出步骤'})).toBeVisible();
 fireEvent.click(screen.getByText('文件与分支自动整理'));expect(screen.getByRole('heading',{name:'节点与资料状态'})).toBeVisible();expect(screen.queryByRole('button',{name:'修改此段'})).toBeNull();
});
it('never exposes editing to viewers and freezes uncertain submissions',()=>{
 const view=render(<DirectoryPanel {...props} object={{...object,can_write:false}}/>);expect(screen.queryByRole('button',{name:'补充 / 修改'})).toBeNull();
 view.rerender(<DirectoryPanel {...props} inputMode text="保留输入" unknown error="结果待核对"/>);
 expect(screen.getByRole('textbox')).toHaveAttribute('readonly');fireEvent.click(screen.getAllByRole('button',{name:'核对保存结果'})[0]);expect(props.onCheck).toHaveBeenCalledOnce();expect(props.onSubmit).not.toHaveBeenCalled();
});

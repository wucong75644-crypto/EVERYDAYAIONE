import { fireEvent,render,screen } from '@testing-library/react';
import { describe,expect,it,vi } from 'vitest';
import { DEFAULT_FORM } from '../../../stores/useDetailPageStore';
import { GenerationSettings } from '../GenerationSettings';
const actions={onChange:vi.fn(),onAnalyze:vi.fn(),onRequirementAssist:vi.fn()};
describe('固定布局设置',()=>{
 it('默认展示14张、拆分说明，数量后显示默认Kimi',()=>{
  render(<GenerationSettings form={DEFAULT_FORM} hasProductImage={false} {...actions}/>);
  const count=screen.getByRole('button',{name:'生成数量'});const model=screen.getByRole('button',{name:'提示词模型'});
  expect(count).toHaveTextContent('14张');expect(screen.getByText('7张主图＋7张详情')).toBeInTheDocument();
  expect(screen.getByText('主图比例 · 详情3:4')).toBeInTheDocument();
  expect(model).toHaveTextContent('Kimi K3');expect(count.compareDocumentPosition(model)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole('button',{name:'开始生成'})).toBeDisabled();
 });
 it('单类型可选15张并切换KIE的GPT',()=>{
  const onChange=vi.fn();render(<GenerationSettings form={{...DEFAULT_FORM,contentType:'main_image',count:7}} models={[{id:'kimi-k3',name:'Kimi K3',available:true,reason:null},{id:'gpt-6-luna',name:'GPT 6 Luna',available:true,reason:null}]} hasProductImage {...actions} onChange={onChange}/>);
  fireEvent.keyDown(screen.getByRole('button',{name:'生成数量'}),{key:'ArrowDown'});fireEvent.click(screen.getByText('15张'));
  expect(onChange).toHaveBeenCalledWith({count:15});
  fireEvent.keyDown(screen.getByRole('button',{name:'提示词模型'}),{key:'ArrowDown'});fireEvent.click(screen.getByText('GPT 6 Luna'));
  expect(onChange).toHaveBeenCalledWith({promptModel:'gpt-6-luna'});
 });
 it('异常Gemini保留说明但不能被选中',()=>{
  const onChange=vi.fn();render(<GenerationSettings form={DEFAULT_FORM} hasProductImage {...actions} onChange={onChange}
   models={[{id:'kimi-k3',name:'Kimi K3',available:true,reason:null},{id:'gemini-3.8-flash',name:'Gemini 3.8 Flash',available:false,status:'abnormal',reason:'Gemini 服务异常'}]}/>);
  fireEvent.keyDown(screen.getByRole('button',{name:'提示词模型'}),{key:'ArrowDown'});
  const unavailable=screen.getByRole('menuitem',{name:'Gemini 3.8 Flash（服务异常）'});
  expect(unavailable).toHaveAttribute('aria-disabled','true');fireEvent.click(unavailable);
  expect(onChange).not.toHaveBeenCalled();
 });
 it('所有类型保留同样控件和要求区',()=>{
  const {rerender}=render(<GenerationSettings form={DEFAULT_FORM} hasProductImage {...actions}/>);
  for(const contentType of ['main_image','detail_page','default'] as const){
   rerender(<GenerationSettings form={{...DEFAULT_FORM,contentType}} hasProductImage {...actions}/>);
   expect(screen.getByRole('button',{name:'生成数量'})).toBeInTheDocument();expect(screen.getByRole('textbox')).toHaveAttribute('placeholder','上传产品图片，并描述产品名称、核心卖点、规格和设计要求…');
   expect(screen.getByText(contentType==='default'?'主图比例 · 详情3:4':'尺寸比例')).toBeInTheDocument();
   expect(screen.getByRole('button',{name:'上传图片'})).toBeInTheDocument();
   expect(screen.getByRole('button',{name:'工作区',exact:true})).toBeInTheDocument();
   expect(screen.getByRole('button',{name:'AI 帮写'})).toBeInTheDocument();
   expect(screen.queryByRole('heading',{name:'产品图片'})).not.toBeInTheDocument();
  }
 });
 it('原输入区继续上传、删除图片和回填文字',()=>{
  const onAdd=vi.fn(),onRemove=vi.fn(),onChange=vi.fn(),onRequirementAssist=vi.fn();
  render(<GenerationSettings form={DEFAULT_FORM} images={[{id:'image-1',category:'product',status:'ready',name:'商品.png',previewUrl:'preview.png',error:null}]} hasProductImage {...actions} onAdd={onAdd} onRemove={onRemove} onChange={onChange} onRequirementAssist={onRequirementAssist}/>);
  expect(screen.getByAltText('商品.png')).toBeInTheDocument();
  expect(screen.getByText('1/9')).toBeInTheDocument();
  const file=new File(['test'],'新增.png',{type:'image/png'});
  fireEvent.change(screen.getByLabelText('上传产品图'),{target:{files:[file]}});
  expect(onAdd).toHaveBeenCalledWith([file]);
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'发财风格'}});
  expect(onChange).toHaveBeenCalledWith({requirement:'发财风格'});
  fireEvent.click(screen.getByRole('button',{name:'AI 帮写'}));
  expect(onRequirementAssist).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'删除 商品.png'}));
  expect(onRemove).toHaveBeenCalledWith('image-1');
 });
 it('拖拽与聚焦后的文件粘贴复用上传入口，普通文字保留默认行为',()=>{
  const onAdd=vi.fn();
  render(<GenerationSettings form={DEFAULT_FORM} hasProductImage {...actions} onAdd={onAdd}/>);
  const textarea=screen.getByRole('textbox');
  const zone=textarea.parentElement!;
  const file=new File(['image'],'产品.png',{type:'image/png'});
  fireEvent.dragEnter(zone,{dataTransfer:{types:['Files']}});
  expect(screen.getByRole('status')).toHaveTextContent('松开即可上传产品图片');
  fireEvent.drop(zone,{dataTransfer:{files:[file]}});
  expect(onAdd).toHaveBeenCalledWith([file]);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  onAdd.mockClear();
  fireEvent.paste(textarea,{clipboardData:{items:[{kind:'file',getAsFile:()=>file}]}});
  expect(onAdd).not.toHaveBeenCalled();
  textarea.focus();
  expect(fireEvent.paste(textarea,{clipboardData:{items:[{kind:'file',getAsFile:()=>file}]}})).toBe(false);
  expect(onAdd).toHaveBeenCalledWith([file]);
  expect(fireEvent.paste(textarea,{clipboardData:{items:[{kind:'string'}]}})).toBe(true);
 });
 it('禁用或满9张时，拖拽和粘贴不能新增图片',()=>{
  const onAdd=vi.fn();
  const {rerender}=render(<GenerationSettings form={DEFAULT_FORM} hasProductImage {...actions} onAdd={onAdd} disabled/>);
  const file=new File(['image'],'产品.png',{type:'image/png'});
  fireEvent.drop(screen.getByRole('textbox').parentElement!,{dataTransfer:{files:[file]}});
  expect(onAdd).not.toHaveBeenCalled();
  const images=Array.from({length:9},(_,i)=>({id:String(i),category:'product' as const,status:'ready' as const,name:`${i}.png`,previewUrl:'preview.png',error:null}));
  rerender(<GenerationSettings form={DEFAULT_FORM} hasProductImage {...actions} onAdd={onAdd} images={images}/>);
  const textarea=screen.getByRole('textbox');
  fireEvent.drop(textarea.parentElement!,{dataTransfer:{files:[file]}});
  textarea.focus();
  fireEvent.paste(textarea,{clipboardData:{items:[{kind:'file',getAsFile:()=>file}]}});
  expect(onAdd).not.toHaveBeenCalled();
 });
});

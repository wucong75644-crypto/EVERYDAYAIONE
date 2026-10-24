import {RichTitle} from '../TitleText';
import {describe,it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {editRuns,formatRange,paintTitle} from '../richTitle';
import type {TitleFormat} from '../../../services/knowledge';
const fmt:TitleFormat={align:'center',runs:[{text:'售后',bold:true},{text:'😀方法',color:'#ff0000'}]};
describe('partial title formatting',()=>{
 it('renders partial styles with safe text and alignment retained through edits',()=>{render(<h3><RichTitle text="售后😀方法" format={fmt}/></h3>);expect(screen.getByText('售后')).toHaveStyle({fontWeight:700});expect(screen.getByText('😀方法')).toHaveStyle({color:'#ff0000'});const f=editRuns('售后😀方法','售后😀新方法',fmt);expect(f.align).toBe('center');expect(f.runs.map(r=>r.text).join('')).toBe('售后😀新方法');expect(f.runs.find(r=>r.text.includes('新'))?.color).toBe('#ff0000');});
 it('uses UTF16 browser selections without splitting emoji and supports explicit unbold',()=>{const f=formatRange('售后😀方法',fmt,2,4,{italic:true,font_size:24});expect(f.runs.find(r=>r.text==='😀')).toMatchObject({italic:true,font_size:24,color:'#ff0000'});expect(formatRange('售后',fmt,0,2,{bold:false}).runs[0].bold).toBe(false);});
 it('drops stale runs and handles deletion and full replacement',()=>{expect(editRuns('售后😀方法','售后',fmt).runs).toEqual([{text:'售后',bold:true}]);expect(editRuns('售后😀方法','计划',fmt).runs.map(r=>r.text).join('')).toBe('计划');const el=document.createElement('h3');paintTitle(el,'<script>',fmt);expect(el.textContent).toBe('<script>');expect(el.querySelector('script')).toBeNull();});
});

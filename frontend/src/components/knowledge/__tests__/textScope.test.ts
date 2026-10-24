import {it,expect} from 'vitest';
import {editTopicStyle,pasteTopicStyle,styleSnapshot} from '../textScope';
import {defaultStyle} from '../canvasStyle';
import {selectionValue} from '../titleEditing';
import type {CanvasData} from '../../../services/knowledge';
const data:CanvasData={note:'保留备注',image:{binding_id:'image',revision:2,width:100,placement:'top',alt:''},title_format:{align:'right',runs:[{text:'A',color:'#ff0000',bold:false},{text:'B',font_size:28}]}};
it('preserves local text and alignment when only the background changes',()=>{const next=editTopicStyle('AB',data,{...defaultStyle,background:'#000000'},defaultStyle);expect(next.title_format).toEqual(data.title_format);expect(next.image).toEqual(data.image);expect(next.note).toBe('保留备注');});
it('whole-title text controls clear only corresponding partial overrides',()=>{const next=editTopicStyle('AB',data,{...defaultStyle,color:'#00ff00'},defaultStyle,undefined,['color']);expect(next.title_format).toEqual({align:'right',runs:[{text:'A',bold:false},{text:'B',font_size:28}]});expect(next.style?.color).toBe('#00ff00');});
it('format brush transfers alignment and whole title styles without copying text or annotation content',()=>{const snapshot=styleSnapshot({style:{...defaultStyle,bold:true},title_format:{align:'center',runs:[{text:'来源',color:'#123456'}]}});const next=pasteTopicStyle('AB',data,snapshot);expect(next.title_format).toEqual({align:'center',runs:[{text:'AB'}]});expect(next.note).toBe('保留备注');expect(next.image).toEqual(data.image);});
it('reset clears local formatting and alignment while retaining content',()=>{const next=editTopicStyle('AB',data,null,defaultStyle);expect(next.title_format).toBeUndefined();expect(next.style).toBeNull();expect(next.note).toBe('保留备注');});
it('a caret at a style boundary inherits from the left, not mixed neighboring styles',()=>{expect(selectionValue('AB',{align:'left',runs:[{text:'A',bold:false},{text:'B',bold:true}]},{start:1,end:1},{fontWeight:700},'bold')).toBe(false);});

it('uses the actual displayed font weight and normalizes browser RGB colors for format controls',async()=>{const {displayedTitleStyle,cssTextColor}=await import('../titleEditing');const heading=document.createElement('h3');heading.style.fontWeight='400';heading.style.fontSize='20px';heading.style.color='rgb(17, 24, 39)';document.body.append(heading);expect(displayedTitleStyle(heading)).toMatchObject({fontWeight:400,fontSize:20,color:'#111827'});expect(cssTextColor('rgb(194, 65, 12)')).toBe('#c2410c');heading.remove();});

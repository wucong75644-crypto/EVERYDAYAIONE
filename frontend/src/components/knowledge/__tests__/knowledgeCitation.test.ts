import {expect,it} from 'vitest';
import {knowledgeCitationHref} from '../knowledgeCitation';
it('keeps model invented hosts out of internal knowledge navigation',()=>{
 const path='/knowledge?object=12345678-1234-1234-1234-123456789abc&revision=2&generation=12345678-1234-1234-1234-123456789abd';
 expect(knowledgeCitationHref('https://km.example.com'+path)).toBe(path);
 expect(knowledgeCitationHref(path)).toBe(path);
});
it('does not alter ordinary external pages or unsafe schemes',()=>{
 expect(knowledgeCitationHref('https://docs.example.com/article')).toBe('https://docs.example.com/article');
 expect(knowledgeCitationHref('javascript:alert(1)')).toBe('javascript:alert(1)');
});

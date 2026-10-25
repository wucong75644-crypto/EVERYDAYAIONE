/** Knowledge citations always open our permission-checked version viewer. */
export function knowledgeCitationHref(href: string | undefined): string | undefined {
 if(!href)return href;
 try {
  const url=new URL(href,'https://knowledge.invalid');
  if(!['http:','https:'].includes(url.protocol)||url.pathname!='/knowledge')return href;
  const keys=['object','revision','binding','generation','chunk'];
  if([...url.searchParams.keys()].some(key=>!keys.includes(key)))return href;
  const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if(!uuid.test(url.searchParams.get('object')??'')||!/^\d+$/.test(url.searchParams.get('revision')??''))return href;
  return url.pathname+url.search;
 }catch{return href;}
}

import type {KnowledgeNode,MapTheme} from '../../services/knowledge';
import type {CardSize} from './branchLayout';
import {nodeHidden} from './summaryScope';
import {arrangeStructure,structureRoot} from './topicStructure';

/** Structured mindmaps lay out their visible content; free topics retain their positions. */
export function mindmapView(nodes:KnowledgeNode[],folded:Set<string>,sizes:Map<string,CardSize>,theme?:Partial<MapTheme>):KnowledgeNode[]{
 const visible=nodes.filter(n=>!nodeHidden(nodes,folded,n));
 const roots=visible.filter(n=>n.canvas_data?.structure&&structureRoot(visible,n.id,theme?.layout)?.id===n.id);
 if(!roots.length)return nodes;
 let arranged=visible;
 for(const root of roots)arranged=arrangeStructure(arranged,root.id,sizes,{compact:true,...theme?.layout},theme);
 const positions=new Map(arranged.map(n=>[n.id,n]));
 return nodes.map(n=>positions.get(n.id)??n);
}

import type {KnowledgeFileVersion} from '../../services/knowledge';
export function fileMeta(v:KnowledgeFileVersion){return `${v.mime_type.startsWith('image/')?'图片':v.mime_type.startsWith('video/')?'视频':v.name.split('.').pop()?.toUpperCase()??'文件'} · ${v.size<1048576?(v.size/1024).toFixed(0)+' KB':(v.size/1048576).toFixed(1)+' MB'} · v${v.revision}`;}

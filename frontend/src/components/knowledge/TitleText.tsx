import type {TitleFormat} from '../../services/knowledge';
import {runsFor,runStyle} from './richTitle';
export function RichTitle({text,format}:{text:string;format?:TitleFormat}) {return <>{runsFor(text,format).map((r,i)=><span key={i} style={runStyle(r)}>{r.text}</span>)}</>;}

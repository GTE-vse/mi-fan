import React,{useEffect,useId,useMemo,useState} from 'react';
import {useChartWidth,timeLabel} from './chart_utils';

export type Reading={t:number;v:number|null};
export function TemperatureChart({history,thresholds,pollSeconds}:{history:Reading[];thresholds:number[];pollSeconds:number}){
 const {ref,width}=useChartWidth();
 const [selectedTime,setSelectedTime]=useState<number|null>(null),[pinned,setPinned]=useState(false);
 const gradient=useId().replace(/:/g,'');
 const points=history.filter(p=>Number.isFinite(p.t)&&(p.v===null||Number.isFinite(p.v)));
 const valid=points.filter((p):p is {t:number;v:number}=>p.v!==null);
 const start=points[0]?.t??0,end=points[points.length-1]?.t??start;
 const left=44,right=width-14,top=28,bottom=186;
 const low=Math.floor(Math.min(35,...valid.map(p=>p.v))/10)*10;
 const high=Math.ceil(Math.max(80,...valid.map(p=>p.v),...thresholds)/10)*10;
 const x=(t:number)=>left+(t-start)/Math.max(end-start,1)*(right-left);
 const y=(v:number)=>bottom-(v-low)/(high-low)*(bottom-top);
 const segments=useMemo(()=>{
  const result:{t:number;v:number}[][]=[];let active:{t:number;v:number}[]=[];
  for(const p of history){
   if(p.v===null||!Number.isFinite(p.v)||!Number.isFinite(p.t)){if(active.length)result.push(active);active=[];continue}
   if(active.length && p.t-active[active.length-1].t>Math.max(pollSeconds*2.5,5)){result.push(active);active=[]}
   active.push({t:p.t,v:p.v});
  }
  if(active.length)result.push(active);return result;
 },[history,pollSeconds]);
 const chosen=points.find(p=>p.t===selectedTime);
 useEffect(()=>{if(selectedTime!==null&&!chosen){setPinned(false);setSelectedTime(null)}},[selectedTime,chosen]);
 const select=(event:React.PointerEvent<SVGRectElement>|React.MouseEvent<SVGRectElement>)=>{
  if(!points.length)return;
  const rect=event.currentTarget.ownerSVGElement!.getBoundingClientRect();
  const svgX=(event.clientX-rect.left)*width/(rect.width||width);
  const t=start+Math.max(0,Math.min(1,(svgX-left)/(right-left)))*(end-start);
  const closest=points.reduce((a,b)=>Math.abs(a.t-t)<=Math.abs(b.t-t)?a:b);
  setSelectedTime(closest.t);
 };
 const keys=(event:React.KeyboardEvent)=>{
  if(!points.length)return;
  const index=points.findIndex(p=>p.t===selectedTime);
  if(event.key==='Escape'){setPinned(false);setSelectedTime(null);return}
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();setPinned(true);
  const next=event.key==='Home'?0:event.key==='End'?points.length-1:Math.max(0,Math.min(points.length-1,(index<0?points.length-1:index)+(event.key==='ArrowLeft'?-1:1)));
  setSelectedTime(points[next].t);
 };
 const tooltipLeft=chosen?Math.max(0,Math.min(width-160,x(chosen.t)+12)):0;
 return <div ref={ref} className="chart interactive-temperature-chart" data-testid="temperature-chart" onKeyDown={keys}>
  <svg width={width} viewBox={`0 0 ${width} 230`} preserveAspectRatio="none" role="img" aria-label="CPU 温度趋势图，横轴为采样时间，纵轴为摄氏温度" onPointerLeave={()=>{if(!pinned)setSelectedTime(null)}}>
   <defs><linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#6c8beb" stopOpacity=".2"/><stop offset="100%" stopColor="#6c8beb" stopOpacity=".015"/></linearGradient></defs>
   {[low,(low+high)/2,high].map(v=><g key={v}><line x1={left} x2={right} y1={y(v)} y2={y(v)} stroke="var(--line)"/><text x="3" y={y(v)+4}>{v}°</text></g>)}
   {thresholds.filter(v=>v>=low&&v<=high).map(v=><line key={v} x1={left} x2={right} y1={y(v)} y2={y(v)} stroke="var(--accent)" opacity=".2" strokeDasharray="4 5"/>)}
   {segments.map((segment,i)=>segment.length===1?<circle key={i} cx={x(segment[0].t)} cy={y(segment[0].v)} r="3" fill="#7091ed"/>:<g key={i} data-testid="temperature-segment">
    <polygon points={`${x(segment[0].t)},${bottom} ${segment.map(p=>`${x(p.t)},${y(p.v)}`).join(' ')} ${x(segment[segment.length-1].t)},${bottom}`} fill={`url(#${gradient})`}/>
    <polyline points={segment.map(p=>`${x(p.t)},${y(p.v)}`).join(' ')} fill="none" stroke="#7091ed" strokeWidth="2.5" strokeLinejoin="round"/>
   </g>)}
   {chosen&&<g pointerEvents="none"><line x1={x(chosen.t)} x2={x(chosen.t)} y1={top} y2={bottom} stroke="var(--muted)" strokeDasharray="3 4"/>{chosen.v!==null&&<circle cx={x(chosen.t)} cy={y(chosen.v)} r="5" fill="var(--accent)" stroke="var(--paper)" strokeWidth="2"/>}</g>}
   <text x={left} y="217">{points.length?timeLabel(start):'等待数据'}</text>
   {end>start&&<text x={right} y="217" textAnchor="end">{timeLabel(end)}</text>}
   <rect data-testid="temperature-hit" x={left} y={top} width={Math.max(1,right-left)} height={bottom-top} fill="transparent" onPointerMove={event=>{if(!pinned)select(event)}} onClick={event=>{select(event);setPinned(v=>!v)}}/>
  </svg>
  {!valid.length&&<div className="chart-empty">正在等待温度数据</div>}
  {chosen&&<div role="tooltip" className="chart-tooltip" style={{left:tooltipLeft,top:0}}><time>{timeLabel(chosen.t)}</time><strong>{chosen.v===null?'温度数据中断':`${chosen.v.toFixed(1)} °C`}</strong></div>}
  <div className="chart-caption"><span>悬停查看 · 点击固定</span><button type="button" className="text-link" onClick={()=>{setSelectedTime(points[points.length-1]?.t??null);setPinned(true)}} disabled={!points.length} aria-label="查看温度采样，左右方向键切换">查看采样</button></div>
 </div>;
}

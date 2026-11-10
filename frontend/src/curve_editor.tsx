import React,{useEffect,useRef,useState} from 'react';
import {Alert,Button,IconButton,Slider,TextField,Tooltip} from '@mui/material';
import {AddRounded,DeleteOutlineRounded} from '@mui/icons-material';
import {useChartWidth} from './chart_utils';

export type CurvePoint={temp_c:number;level:number};
export type CurvePreview={ok:boolean;error?:string;steps?:CurvePoint[];ideal_level?:number;target_level?:number;reference_rpm?:number|null};
export const DEFAULT_CURVE:CurvePoint[]=[{temp_c:40,level:0},{temp_c:55,level:0},{temp_c:65,level:1},{temp_c:75,level:2},{temp_c:85,level:3}];
const measured:Record<number,number>={0:348,1:700,2:746,3:799};
function levelMax(model:string){if(model==='zhimi.airpurifier.ma2')return 14;if(['zhimi.airpurifier.v5','zhimi.airpurifier.v6','zhimi.airpurifier.v7','zhimi.airpurifier.mc1','zhimi.airpurifier.mc2'].includes(model))return 100;return 16;}
function errorFor(points:CurvePoint[],maxLevel:number){
 if(points.length<2||points.length>12)return '请保留 2–12 个控制点。';
 for(let i=0;i<points.length;i++){
  const p=points[i];if(!Number.isFinite(p.temp_c)||p.temp_c<=0||p.temp_c>=120)return '温度必须在 0–120°C 之间。';
  if(!Number.isInteger(p.level)||p.level<0||p.level>maxLevel)return `档位必须是 0–${maxLevel} 的整数。`;
  if(i&&p.temp_c<=points[i-1].temp_c)return '温度必须递增，不能重复。';
  if(i&&p.level<points[i-1].level)return '温度升高时，档位不能降低。';
 }
 return '';
}

export function CurveEditor({points,onChange,locked,model='zhimi.airpurifier.m1'}:{points:CurvePoint[];onChange:(points:CurvePoint[])=>void;locked:boolean;model?:string}){
 const effectiveMax=levelMax(model);
 const {ref,width}=useChartWidth();const [temperature,setTemperature]=useState(60),[preview,setPreview]=useState<CurvePreview|null>(null);
 const [selected,setSelected]=useState(0);const dragging=useRef<number|null>(null);
 const fixedDomain=useRef<{minT:number;maxT:number;maxLevel:number}|null>(null);
 const [,refreshDomain]=useState(0);
 const error=errorFor(points,effectiveMax);const generation=useRef(0);
 useEffect(()=>{
  const id=++generation.current;setPreview(null);if(error)return;
  const timer=setTimeout(()=>{
   window.pywebview?.api.preview_curve(points,temperature).then(result=>{if(generation.current===id)setPreview(result)}).catch(()=>{if(generation.current===id)setPreview({ok:false,error:'曲线预览暂不可用，请重试。'})});
  },100);
  return()=>{clearTimeout(timer);generation.current++};
 },[points,temperature,error]);
 useEffect(()=>{if(locked)dragging.current=null},[locked]);
 const left=44,right=width-18,top=32,bottom=218;
 const minT=fixedDomain.current?.minT??Math.max(0,Math.floor(Math.min(...points.map(p=>p.temp_c).filter(Number.isFinite))/10)*10-10);
 const maxT=fixedDomain.current?.maxT??Math.min(120,Math.max(minT+10,Math.ceil(Math.max(...points.map(p=>p.temp_c).filter(Number.isFinite))/10)*10+10));
 const maxLevel=fixedDomain.current?.maxLevel??Math.min(effectiveMax,Math.max(4,...points.map(p=>p.level+2).filter(Number.isFinite)));
 const x=(t:number)=>left+(t-minT)/(maxT-minT)*(right-left);
 const y=(level:number)=>bottom-level/maxLevel*(bottom-top);
 const dragPoint=(index:number,temp:number,level:number)=>{
  if(locked)return;
  const low=index?points[index-1].temp_c+.1:1,high=index<points.length-1?points[index+1].temp_c-.1:119;
  const lowLevel=index?points[index-1].level:0,highLevel=index<points.length-1?points[index+1].level:effectiveMax;
  const t=low<=high?Math.max(low,Math.min(high,Math.round(temp*10)/10)):points[index].temp_c;
  onChange(points.map((p,i)=>i===index?{temp_c:Number(t.toFixed(1)),level:Math.max(lowLevel,Math.min(highLevel,Math.round(level)))}:p));
 };
 const coordinates=(event:React.PointerEvent<SVGSVGElement>|React.MouseEvent<SVGSVGElement>)=>{
  const rect=event.currentTarget.getBoundingClientRect();
  const px=(event.clientX-rect.left)*width/(rect.width||width),py=(event.clientY-rect.top)*260/(rect.height||260);
  return {temp:minT+(px-left)/(right-left)*(maxT-minT),level:(bottom-py)/(bottom-top)*maxLevel};
 };
 const add=(requested?:{temp:number;level:number})=>{
  if(locked||points.length>=12||error)return;
  let temp:number,level:number;
  if(requested){temp=Math.round(Math.max(1,Math.min(119,requested.temp))*10)/10;level=Math.round(requested.level)}
  else {let gap=0,index=0;for(let i=0;i<points.length-1;i++){const size=points[i+1].temp_c-points[i].temp_c;if(size>gap){gap=size;index=i}}temp=Math.round((points[index].temp_c+points[index+1].temp_c)*5)/10;level=Math.floor((points[index].level+points[index+1].level)/2)}
  if(points.some(p=>Math.abs(p.temp_c-temp)<.09))return;
  const index=points.findIndex(p=>p.temp_c>temp),insert=index<0?points.length:index;
  level=Math.max(insert?points[insert-1].level:0,Math.min(insert<points.length?points[insert].level:effectiveMax,level));
  const next=[...points];next.splice(insert,0,{temp_c:temp,level});onChange(next);setSelected(insert);
 };
 const remove=(index:number)=>{if(locked||points.length<=2)return;onChange(points.filter((_,i)=>i!==index));setSelected(Math.max(0,index-1))};
 const finishDrag=()=>{dragging.current=null;fixedDomain.current=null;refreshDomain(v=>v+1)};
 const ticks=width<430?[minT,(minT+maxT)/2,maxT]:[minT,minT+(maxT-minT)/3,minT+2*(maxT-minT)/3,maxT];
 return <section className="panel curve-editor">
  <div className="panel-heading"><div><h3>自定义温度曲线</h3><p>拖动控制点，或在下方输入数值 · 双击图表添加</p></div><Button startIcon={<AddRounded/>} onClick={()=>add()} disabled={locked||!!error||points.length>=12}>添加控制点</Button></div>
  <div className="curve-legend"><span><i/>理想曲线</span><span><i className="step-line"/>目标档位（未计回差与延时）</span></div>
  <div ref={ref} className="curve-chart-wrap">
   <svg className="curve-chart" width={width} height="260" viewBox={`0 0 ${width} 260`} aria-label="自定义温度与档位曲线" role="img"
    onDoubleClick={event=>{if(event.target instanceof Element&&event.target.closest('[data-point]'))return;const p=coordinates(event);if(p.temp>=minT&&p.temp<=maxT&&p.level>=0&&p.level<=maxLevel)add(p)}}
    onPointerMove={event=>{if(dragging.current!==null){const p=coordinates(event);dragPoint(dragging.current,p.temp,p.level)}}}
    onPointerUp={event=>{finishDrag();if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId)}}
    onPointerCancel={finishDrag} onLostPointerCapture={finishDrag}>
    {[0,Math.round(maxLevel/2),maxLevel].map(v=><g key={v}><line x1={left} x2={right} y1={y(v)} y2={y(v)} stroke="var(--line)"/><text x="8" y={y(v)+4}>{v}</text></g>)}
    <text x={left} y="17">风量档位</text><text x={right} y="17" textAnchor="end">CPU 温度 °C</text>
    {ticks.map((v,i)=><text key={i} x={x(v)} y="248" textAnchor={i===0?'start':i===ticks.length-1?'end':'middle'}>{Math.round(v)}°</text>)}
    {!error&&<><polyline points={[`${left},${y(points[0].level)}`,...points.map(p=>`${x(p.temp_c)},${y(p.level)}`),`${right},${y(points[points.length-1].level)}`].join(' ')} fill="none" stroke="var(--accent)" strokeWidth="2.5"/>
     {preview?.ok&&preview.steps&&<polyline data-testid="curve-step-line" points={[`${left},${y(points[0].level)}`,...preview.steps.map(p=>`${x(p.temp_c)},${y(p.level)}`),`${right},${y(points[points.length-1].level)}`].join(' ')} fill="none" stroke="var(--curve-step)" strokeWidth="2" strokeDasharray="5 4"/>}
     {preview?.ok&&temperature>=minT&&temperature<=maxT&&<><line x1={x(temperature)} x2={x(temperature)} y1={top} y2={bottom} stroke="var(--muted)" strokeDasharray="3 4"/><rect x={x(temperature)-4} y={y(preview.target_level!)-4} width="8" height="8" fill="var(--curve-step)"/></>}
     {points.map((p,i)=><g key={i} data-point={i} className={locked?'':'draggable-point'} onPointerDown={event=>{if(locked)return;event.preventDefault();fixedDomain.current={minT,maxT,maxLevel};dragging.current=i;setSelected(i);event.currentTarget.ownerSVGElement!.setPointerCapture(event.pointerId)}}>
      <circle cx={x(p.temp_c)} cy={y(p.level)} r="16" fill="transparent"/><circle cx={x(p.temp_c)} cy={y(p.level)} r={selected===i?7:5} fill="var(--paper)" stroke="var(--accent)" strokeWidth="2.5"/>
      <title>{p.temp_c}°C → {p.level} 档</title>
     </g>)}
    </>}
   </svg>
  </div>
  {(error||preview?.error)&&<Alert severity="warning">{error||preview?.error}</Alert>}
  <div className="curve-preview"><label id="curve-preview-label">模拟温度 <b>{temperature.toFixed(1)}°C</b></label><Slider aria-labelledby="curve-preview-label" value={temperature} min={0} max={119} step={.1} onChange={(_,v)=>setTemperature(v as number)} valueLabelDisplay="auto"/>
   <div aria-live="polite" data-testid="curve-preview-result">{preview?.ok?`计算值 ${preview.ideal_level!.toFixed(2)} 档 → 目标 ${preview.target_level} 档${preview.reference_rpm!=null?` · 该档实测约 ${preview.reference_rpm} RPM`:' · 该档 RPM 尚未标定'}`:'等待有效曲线…'}</div>
  </div>
  <div className="curve-point-list">{points.map((point,i)=><div key={i} className={`curve-point-row ${selected===i?'selected':''}`} onFocus={()=>setSelected(i)}>
   <span className="point-index">{String(i+1).padStart(2,'0')}</span>
   <TextField label={`控制点 ${i+1} 温度（°C）`} type="number" value={point.temp_c} disabled={locked} inputProps={{min:1,max:119,step:.1}} onChange={event=>onChange(points.map((p,j)=>i===j?{...p,temp_c:Number(event.target.value)}:p))}/>
   <TextField label={`控制点 ${i+1} 档位`} type="number" value={point.level} disabled={locked} inputProps={{min:0,max:effectiveMax,step:1}} onChange={event=>onChange(points.map((p,j)=>i===j?{...p,level:Number(event.target.value)}:p))}/>
   <span className="point-rpm">{measured[point.level]!=null?`约 ${measured[point.level]} RPM`:'RPM 未标定'}</span>
   <Tooltip title="删除控制点"><span><IconButton aria-label={`删除控制点 ${i+1}`} disabled={locked||points.length<=2} onClick={()=>remove(i)}><DeleteOutlineRounded/></IconButton></span></Tooltip>
  </div>)}</div>
  <div className="info-line">0 档仍然转动。参考 RPM 来自这台设备的 0–3 档实测；曲线无法产生档位之间不存在的转速。</div>
 </section>;
}

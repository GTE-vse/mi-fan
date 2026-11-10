import {useEffect,useRef,useState} from 'react';

export function useChartWidth(){
 const ref=useRef<HTMLDivElement>(null),[width,setWidth]=useState(640);
 useEffect(()=>{
  const update=()=>{if(ref.current?.clientWidth)setWidth(Math.max(240,ref.current.clientWidth))};
  update();const observer=new ResizeObserver(update);if(ref.current)observer.observe(ref.current);
  return()=>observer.disconnect();
 },[]);
 return {ref,width};
}
export const timeLabel=(seconds:number)=>new Date(seconds*1000).toLocaleTimeString('zh-CN',{hour12:false});

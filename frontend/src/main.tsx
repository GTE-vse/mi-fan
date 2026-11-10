import React,{useEffect,useMemo,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ThemeProvider,createTheme,CssBaseline,Button,IconButton,Tooltip,Switch,TextField,Slider,
  Snackbar,Alert,Chip,MenuItem,Select,LinearProgress,Divider,CircularProgress} from '@mui/material';
import {DashboardRounded,TuneRounded,DevicesRounded,ReceiptLongRounded,SettingsRounded,
  AirRounded,PowerSettingsNewRounded,PlayArrowRounded,StopRounded,RefreshRounded,LightModeRounded,
  DarkModeRounded,ThermostatRounded,SpeedRounded,CheckCircleRounded,ArrowForwardRounded,
  FolderOpenRounded,InfoOutlined,SaveRounded,MonitorHeartRounded,Menu as MenuIcon,BuildRounded,
  NotificationsNoneRounded,LockRounded,FavoriteRounded,NightlightRounded,ThermostatAutoRounded,
  Tune as TuneIcon,AcUnitRounded} from '@mui/icons-material';
const RiceBowlIcon=()=><span style={{display:'inline-flex',fontSize:22}}><AirRounded fontSize="inherit"/></span>;
import './style.css';
import './curve.css';
import {CurveEditor,DEFAULT_CURVE,type CurvePoint,type CurvePreview} from './curve_editor';
import {TemperatureChart} from './temperature_chart';

type Config={ip:string;model:string;sensor_name:string;lhm_url:string;lhm_path:string;thresholds_c:number[];
favorite_levels:number[];hysteresis_c:number;hold_seconds:number;poll_seconds:number;restore_auto_on_exit:boolean;
auto_start_lhm:boolean;theme:'system'|'light'|'dark';has_token:boolean;token?:string;control_mode:'bands'|'curve';curve_points:CurvePoint[];autostart?:boolean;tray_minimize?:boolean};
type ExtractorState={running:boolean;status:'idle'|'running'|'success'|'failed'|'empty'|'timeout'|'cancelled';result?:{ip:string;model:string}|null;message:string};
type DeviceState={temp:number|null;target:number|null;power:boolean|null;mode:string|null;rpm:number|null;device_level:number|null;device_connected:boolean;linked:boolean;sensor:string;detail:string;updated:string|null;error:string|null;
pm25:number|null;humidity:number|null;filter_life:number|null;filter_days:number|null;led:string|null;buzzer:string|null;child_lock:string|null;temp_dec:number|null;speed:number|null};
type SetDeviceParams={mode?:string;level?:number;led?:boolean;buzzer?:boolean;child_lock?:boolean};
type HardwareInfo={cpu:string;gpu:string;dram:string;hostname:string};
type Snapshot={version:string;busy:boolean;closing:boolean;config:Config;state:DeviceState;
history:{t:number;v:number|null}[];logs:{id:number;time:string;level:string;text:string}[];extractor:ExtractorState;hardware:HardwareInfo;human_model?:string};
declare global {interface Window{pywebview?:{api:{snapshot:()=>Promise<Snapshot>;action:(n:string)=>Promise<{ok:boolean,error?:string}>;
set_device:(p:SetDeviceParams)=>Promise<{ok:boolean,error?:string}>;
save_settings:(v:Partial<Config>)=>Promise<{ok:boolean,error?:string}>;choose_lhm:()=>Promise<string|null>;ui_ready?:()=>Promise<boolean>;preview_curve:(points:CurvePoint[],temperature:number)=>Promise<CurvePreview>;set_caption:(dark:boolean)=>Promise<boolean>;
toggle_autostart:(enable:boolean)=>Promise<boolean>;toggle_tray_minimize:(enable:boolean)=>Promise<boolean>;get_tray_status:()=>Promise<{autostart:boolean,tray_minimize:boolean}>;show_window:()=>Promise<boolean>;hide_window:()=>Promise<boolean>;handle_minimize_to_tray:()=>Promise<boolean>}}}}
const pages=[{key:'home',label:'概览',icon:DashboardRounded,subtitle:'空气与温度，尽在掌握'},
{key:'rules',label:'温度联动',icon:TuneRounded,subtitle:'让风量随电脑温度自然变化'},
{key:'device',label:'设备连接',icon:DevicesRounded,subtitle:'管理净化器与温度监测'},
{key:'logs',label:'运行日志',icon:ReceiptLongRounded,subtitle:'每一次连接与调整，都有迹可循'},
{key:'settings',label:'偏好设置',icon:SettingsRounded,subtitle:'按照你的习惯，打造自己的 Mi Fan'}];
const modeName=(s:string|null)=>({auto:'自动模式',favorite:'收藏模式',silent:'睡眠模式'}[s||'']||s||'—');
const levelMax=(model:string,cfg?:any)=>{
 // 优先用 config 里缓存的探测结果（后端 probe_level_max 存的）
 if(cfg?.level_max) return parseInt(cfg.level_max,10)||16;
 // 否则用硬编码型号映射（和后端 controller.py 保持同步）
 if(model==='zhimi.airpurifier.ma2'||model==='zhimi.airpurifier.ma4') return 14;
 if(['zhimi.airpurifier.m1','zhimi.airpurifier.m2','zhimi.airpurifier.ma1','zhimi.airpurifier.mb1',
     'zhimi.airpurifier.mb2','zhimi.airpurifier.ma3','zhimi.airpurifier.ma5','zhimi.airpurifier.ma6',
     'zhimi.airpurifier.mc1','zhimi.airpurifier.mc2'].includes(model)) return 16;
 if(['zhimi.airpurifier.v5','zhimi.airpurifier.v6','zhimi.airpurifier.v7','zhimi.airpurifier.v8',
     'zhimi.airpurifier.pro','zhimi.airpurifier.proh','zhimi.airpurifier.pro2','zhimi.airpurifier.pro2h',
     'zhimi.airpurifier.xa1'].includes(model)) return 100;
 return 16; // unknown 型号，后端连接后会自动探测覆盖
};

function App(){
 const [snap,setSnap]=useState<Snapshot|null>(null),[draft,setDraft]=useState<Config|null>(null),[page,setPage]=useState('home');
 const [working,setWorking]=useState(false),[notice,setNotice]=useState<{text:string,error?:boolean,key:number}|null>(null);
 const [systemDark,setSystemDark]=useState(matchMedia('(prefers-color-scheme:dark)').matches); const [dirty,setDirty]=useState(false),[filter,setFilter]=useState('user'),[sidebarCollapsed,setSidebarCollapsed]=useState(false);
 const [sliderLevel,setSliderLevel]=useState<number|null>(null);
 const [splash,setSplash]=useState(true),[splashFade,setSplashFade]=useState(false),[splashBarDone,setSplashBarDone]=useState(false);
 const splashStartRef=useRef(Date.now());
 const noticeQueue=useRef<{text:string,error?:boolean}[]>([]),noticeKey=useRef(0),actioning=useRef(false);
 const pending=useRef(false),initialized=useRef(false),snapRef=useRef<any>(null);
 useEffect(()=>{const mq=matchMedia('(prefers-color-scheme:dark)');const changed=()=>setSystemDark(mq.matches);mq.addEventListener('change',changed);return()=>mq.removeEventListener('change',changed)},[]);
 useEffect(()=>{if(sliderLevel!=null && snap?.state?.device_level===sliderLevel)setSliderLevel(null)},[sliderLevel,snap?.state?.device_level]);
 useEffect(()=>{let live=true;const started=Date.now();const poll=async()=>{if(pending.current)return;
  if(!window.pywebview?.api){if(Date.now()-started>15000)pushNotice('桌面服务未连接，请关闭后重新打开 Mi Fan。',true);return;}
  pending.current=true;try{const data=await window.pywebview.api.snapshot();if(live){setSnap(data);snapRef.current=data;
   if(!initialized.current){setDraft({...data.config,control_mode:data.config.control_mode??'bands',curve_points:data.config.curve_points??DEFAULT_CURVE,token:''});initialized.current=true;window.pywebview.api.ui_ready?.().catch(()=>{});
    if(!data.config.has_token||!data.config.ip){/* 不自动跳 device，让用户在首页看到引导卡片 */}
    // 等数据真的 ready 再淡出：
    // 1) DOM 有了 .home-root 或 .device-layout
    // 2) 如果配了 Libre 硬件 → 等 s.temp != null
    //    如果没配 → 只要 snapshot 有 updated 时间就行
    // 3) 双层 rAF 等 React 渲染
    // 4) 至少 1.5s 展示时间（进度条 fill 需要）
    const needsLibre=Boolean(data.config.auto_start_lhm||data.config.lhm_path);
    const startTime=Date.now();
    const checker=setInterval(()=>{
     const homeReady=!!document.querySelector('.home-root,.device-layout');
     const latestSnap=snapRef.current;
     const sNow=(latestSnap?.state??{});
     const dataReady=needsLibre?(sNow.temp!=null):!!latestSnap?.updated;
     if(homeReady && dataReady){
      clearInterval(checker);
      // 进度条先填满（splash-fill-done 动画 .5s）
      setSplashBarDone(true);
      requestAnimationFrame(()=>{
       requestAnimationFrame(()=>{
        const elapsed=Date.now()-splashStartRef.current;
        const waitMs=Math.max(500,1500-elapsed); // 最少等进度条填满动画播完
        setTimeout(()=>{setSplashFade(true);setTimeout(()=>setSplash(false),500)},waitMs);
       });
      });
     } else if(Date.now()-startTime>15000){
      clearInterval(checker);
      setSplashBarDone(true);
      requestAnimationFrame(()=>{
       requestAnimationFrame(()=>{setSplashFade(true);setTimeout(()=>setSplash(false),500)});
      });
     }
    },100);
   }
   else if(data.extractor?.status==='success' && data.extractor.result && !data.config.has_token && draft){const ex=data.extractor.result;
    setDraft(d=>d?{...d,ip:ex.ip,model:ex.model||d.model} as Config:d);pushNotice('已自动提取 IP 和 Token，请确认后点击保存设置');setDirty(true);}}}
  catch{if(live)pushNotice('桌面服务连接中断，请重新打开程序。',true)}finally{pending.current=false}};
  poll();const timer=setInterval(poll,800);return()=>{live=false;clearInterval(timer)}},[]);
 const themeSetting=draft?.theme||'system',dark=themeSetting==='dark'||(themeSetting==='system'&&systemDark);
 // 标题栏颜色跟随主题变化（系统深浅切换、手动切换、设置页下拉框都能触发）
 useEffect(()=>{try{window.pywebview?.api?.set_caption(dark)}catch{}},[dark]);
 const theme=useMemo(()=>createTheme({palette:{mode:dark?'dark':'light',primary:{main:dark?'#ffb584':'#FF6700'},
 background:{default:dark?'#1a1410':'#FFF8F0',paper:dark?'#261e18':'#ffffff'},text:{primary:dark?'#f5ebe0':'#2a1f18',secondary:dark?'#b8a89a':'#8a7a70'},
 divider:dark?'#303745':'#e9edf3'},shape:{borderRadius:12},typography:{fontFamily:'"Segoe UI", "Microsoft YaHei UI", sans-serif',fontSize:13,button:{textTransform:'none',fontWeight:600}},
 components:{MuiButton:{defaultProps:{disableElevation:true},styleOverrides:{root:{borderRadius:9,padding:'9px 17px'},contained:{'&.Mui-disabled':{background:dark?'#343e55':'#e1e8f5',color:dark?'#b5c4e1':'#647590'}}}},
 MuiTextField:{defaultProps:{size:'small'}},MuiOutlinedInput:{styleOverrides:{root:{fontSize:14}}},MuiChip:{styleOverrides:{root:{borderRadius:7,fontSize:12}}}}}),[dark]);
 const s=(snap?.state ?? {}) as DeviceState,busy=working||Boolean(snap?.busy)||Boolean(snap?.closing),locked=busy||Boolean(s.linked);
 const update=(key:keyof Config,value:unknown)=>{setDraft(d=>d?{...d,[key]:value}:d);setDirty(true)};
 const pushNotice=(text:string,error?:boolean)=>{noticeQueue.current.push({text,error});if(!notice)showNext();};
 const showNext=()=>{const n=noticeQueue.current.shift();if(n){noticeKey.current++;setNotice({...n,key:noticeKey.current});}};
 const saveNotice=()=>setNotice(null);
 const save=async()=>{if(!draft)return;setWorking(true);try{const res=await window.pywebview!.api.save_settings(draft);if(!res.ok)throw Error(res.error);setDirty(false);setDraft(d=>d?{...d,has_token:d.has_token||Boolean(d.token),token:''}:d);pushNotice('设置已保存');}catch(e){pushNotice(String(e),true)}finally{setWorking(false)}};
 const action=async(name:string)=>{if(actioning.current)return;actioning.current=true;setWorking(true);try{const res=await window.pywebview!.api.action(name);if(!res.ok)throw Error(res.error);pushNotice(({on:'净化器已开启',off:'净化器已关闭',start:'已开始温度联动',stop:'联动已停止',reconnect:'正在重新连接温度',extract_token:'已打开独立 Token 提取工具',auto_extract:'已启动自动提取，请在弹出的窗口里登录',cancel_extract:'已取消自动提取',mode_auto:'已切换到自动模式',mode_silent:'已切换到睡眠模式',mode_favorite:'已切换到最爱模式',mode_strong:'已切换到强力模式',mode_next:'已切换模式'} as Record<string,string>)[name]||'操作成功');}catch(e){pushNotice(String(e),true)}finally{actioning.current=false;setWorking(false)}};
 const setDevice=async(params:SetDeviceParams)=>{if(actioning.current)return;actioning.current=true;setWorking(true);try{const res=await window.pywebview!.api.set_device(params);if(!res.ok)throw Error(res.error);}catch(e){pushNotice(String(e),true)}finally{actioning.current=false;setWorking(false)}};
 const themeToggle=async()=>{const next=dark?'light':'dark';try{await window.pywebview!.api.save_settings({theme:next});setDraft(d=>d?{...d,theme:next}:d);window.pywebview!.api.set_caption(next==='dark');}catch(e){pushNotice(String(e),true)}};
 const header=pages.find(p=>p.key===page)!;
 function Field({label,k,type='text',helper}:{label:string;k:keyof Config;type?:string;helper?:string}){return <TextField fullWidth label={label} type={type} value={draft?.[k]??''} disabled={locked} helperText={helper} onChange={e=>update(k,type==='number'?Number(e.target.value):e.target.value)}/>}
 const field=(label:string,k:keyof Config,type='text',helper?:string)=><TextField fullWidth label={label} type={type} value={draft?.[k]??''} disabled={locked} helperText={helper} onChange={e=>update(k,type==='number'?Number(e.target.value):e.target.value)}/>;
 const saveFooter=<div className="save-footer"><span>{s?.linked?'停止联动后可修改设置':dirty?'有未保存的更改':'设置已同步'}</span><Button startIcon={<SaveRounded/>} variant="contained" onClick={save} disabled={locked||!dirty}>保存设置</Button></div>;
 const connectionGood=s?.device_connected;
 const deviceReady=Boolean(snap?.config.has_token&&snap?.config.ip);
 return <ThemeProvider theme={theme}><CssBaseline/><div className={`app ${dark?'dark':'light'} ${sidebarCollapsed?'sidebar-collapsed':''}`}>
 {splash && <div className={`splash-overlay ${splashFade?'splash-fade':''}`} data-testid="splash">
  <div className="splash-card">
   <div className="splash-logo"><AirRounded/></div>
   <div className="splash-brand">Mi Fan</div>
   <div className="splash-tagline">米饭 · 让设备协同，让空气流动</div>
   <div className="splash-bar"><div className={"splash-bar-fill"+(splashBarDone?" done":"")}/></div>
  </div>
 </div>}
  <aside className="sidebar" data-collapsed={sidebarCollapsed}>
    {/* Logo 区：展开=图标+Mi Fan 同一排，折叠=只有图标 */}
    <div className="brand-area">
      <div className="brand-row">
        <div className="brand-icon-only"><AirRounded/></div>
        {!sidebarCollapsed&&<div className="brand-text">
          <span className="brand-title">Mi Fan</span>
          <span className="brand-sub">米饭</span>
        </div>}
      </div>
      {/* 汉堡按钮单独一行 */}
      <button className="sidebar-toggle" onClick={()=>setSidebarCollapsed(v=>!v)} title={sidebarCollapsed?'展开菜单':'折叠菜单'} aria-label="折叠侧边栏">
        <MenuIcon fontSize="small"/>
      </button>
    </div>
    <nav className="nav-vertical">
      {pages.map(p=>(
        <button key={p.key} className={`nav-icon ${page===p.key?'selected':''}`} title={sidebarCollapsed?p.label:undefined} onClick={()=>setPage(p.key)}>
          <p.icon fontSize="small"/>{p.key==='rules'&&s?.linked&&<span className="nav-dot"/>}
          {!sidebarCollapsed&&<span className="nav-icon-label">{p.label}</span>}
        </button>
      ))}
    </nav>
    <div className="sidebar-foot">
      {!sidebarCollapsed&&<Chip size="small" label={connectionGood?'ON':'OFF'} color={connectionGood?'success':'default'} sx={{fontSize:10,height:18}}/>}
    </div>
  </aside>
  <div className="workspace">
    <header>
      <div className="header-tabs">
        <h1 className="page-title">{header.label}</h1>
        <span className="page-sub">{header.subtitle}</span>
      </div>
      <div className="header-actions">
        {s.linked&&<span className="linked-badge"><span className="linked-dot"/>温度联动运行中</span>}
        <Tooltip title={dark?'切换浅色主题':'切换深色主题'}>
          <IconButton aria-label="切换主题" onClick={themeToggle} disabled={working} size="small">
            {dark?<LightModeRounded fontSize="inherit"/>:<DarkModeRounded fontSize="inherit"/>}
          </IconButton>
        </Tooltip>
      </div>
    </header>
   {(busy||!snap)&&<LinearProgress className="top-progress"/>}
   <main>{snap?.closing&&<Alert severity="info" sx={{mb:2}}>正在停止联动、恢复设备模式并退出自动启动的 Libre…</Alert>}
   {!snap||!draft?<div className="loading">正在连接本机服务…</div>:<>
   {page==='home'&&<div className="page-enter">
    {/* 无设备：左栏设备卡位置显示引导内容，右栏隐藏 */}
    {!deviceReady ? (
      <div data-testid="dashboard" className="home-root">
       <div className="home-col-left">
        <section className="au-device-card welcome-device-card">
          <div className="welcome-illust-big"><DevicesRounded/></div>
          <div className="welcome-device-text">
            <h2>添加你的第一台净化器</h2>
            <p>Mi Fan 需要连接同一局域网内的米家空气净化器才能开始工作。点击下方按钮，Mi Fan 会引导你完成快速连接。</p>
            <div className="welcome-actions">
              <Button variant="contained" size="large" onClick={()=>setPage('device')} startIcon={<DevicesRounded/>}>前往设备连接</Button>
              <span className="welcome-hint">只需 30 秒 · 自动提取 IP 与 Token</span>
            </div>
          </div>
        </section>
       </div>
       <div className="home-col-right home-col-empty">
        <section className="chart-panel">
          <div className="panel-heading"><div><h3>温度趋势</h3><p>连接设备后显示 CPU 温度曲线</p></div></div>
          <div className="empty-state"><ThermostatRounded fontSize="large"/><span>等待设备连接</span></div>
        </section>
        <section className="sys-monitor">
          <div className="sys-monitor-grid">
            <div className="sys-block">
              <div className="sys-block-title"><ThermostatRounded fontSize="inherit"/> TEMPERATURE</div>
              <div className="sys-row"><span>CPU 温度</span><b>—</b></div>
              <div className="sys-row"><span>设备温度</span><b>—</b></div>
              <div className="sys-row sys-row-narrow"><span>更新时间</span><b>{s.sensor&&s.sensor!=='正在连接'&&!s.sensor.includes('正在')?s.sensor:'等待启动'}</b></div>
            </div>
            <div className="sys-block">
              <div className="sys-block-title"><SpeedRounded fontSize="inherit"/> FAN</div>
              <div className="sys-row"><span>风扇转速</span><b>— RPM</b></div>
              <div className="sys-row"><span>运行模式</span><b>OFFLINE</b></div>
              <div className="sys-row"><span>目标档位</span><b>— / {levelMax(snap.config.model, snap.config)}</b></div>
            </div>
            <div className="sys-block">
              <div className="sys-block-title"><AirRounded fontSize="inherit"/> AIR</div>
              <div className="sys-row"><span>PM2.5</span><b>— μg/m³</b></div>
              <div className="sys-row"><span>湿度</span><b>— %</b></div>
              <div className="sys-row"><span>滤芯寿命</span><b>— %</b></div>
            </div>
          </div>
        </section>
        <section className="op-mode-panel">
          <div className="op-mode-label">OPERATING MODE</div>
          <div className="op-mode-row"><span className="op-empty">连接设备后可用</span></div>
        </section>
       </div>
      </div>
    ) : (
    <div data-testid="dashboard" className="home-root">
    {/* === 左栏：DEVICE 大卡 + PERIPH 控制器 === */}
    <div className="home-col-left">
    <section className="au-device-card">
        {/* 净化器插画区，flex:1 撑满空间 */}
        <div className="au-device-hero" aria-hidden="true">
          <svg viewBox="0 0 120 132" className="air-art">
            {/* 机身方框（净化器上视图） */}
            <rect x="22" y="50" width="76" height="78" rx="6" fill="var(--paper-2)" stroke="var(--line)"/>
            <rect x="28" y="56" width="64" height="60" rx="3" fill="var(--bg)" stroke="var(--line)"/>
            {/* 进风格子 */}
            <g stroke="var(--line-2)" strokeWidth="1" opacity=".6">
              {Array.from({length:6},(_,i)=><line key={'v'+i} x1={34+i*12} y1={62} x2={34+i*12} y2={106}/>)}
              {Array.from({length:5},(_,i)=><line key={'h'+i} x1={34} y1={66+i*10} x2={94} y2={66+i*10}/>)}
            </g>
            {/* 风扇叶片 —— 用 fan.png */}
            {(() => {
              const running = s.power!==false && s.mode!=='off';
              // 动画速度跟随挡位：0档=极慢，14档=极快，线性映射
              const level = s.device_level ?? 0;
              const dur = running ? `${Math.max(0.6, 7 - level * 0.45).toFixed(1)}s` : '7s';
              return (
              <image className={'air-fan-img' + (running ? ' air-fan-spin' : '')}
                     x="42" y="65" width="36" height="36"
                     href="/fan.png"
                     style={{'--fan-dur': dur} as React.CSSProperties}/>);
            })()}
          </svg>
          {/* 状态呼吸灯 */}
          {s.power!==false&&s.mode!=='off'&&<div className="air-glow" style={{boxShadow:'0 0 30px 6px var(--accent-dim)'}}/>}
        </div>
        <div className="au-device-info">
          <div className="au-device-name">{snap.human_model||snap.config.model||'Mi Air Purifier'}</div>
          {snap.hardware?.cpu&&<div className="au-device-cpu">{snap.hardware.cpu}</div>}
          <div className="au-device-mem">
            {snap.hardware?.gpu||'—'}
            {(snap.hardware?.gpu||snap.hardware?.dram)?' · ':''}
            {snap.hardware?.dram||''}
          </div>
        </div>
        <div className="au-device-foot">
          <span className="au-foot-label">STATUS</span>
          <div className="au-foot-right">
            <span className={`au-foot-val ${connectionGood?'good':'bad'}`}>{s.power===false?'OFFLINE':s.mode?modeName(s.mode):'POWER OFF'}</span>
            {s.device_connected&&<button className={`au-power-btn ${s.power?'on':'off'}`} disabled={busy||s.linked} onClick={()=>action(s.power?'off':'on')} title={s.power?'关机':'开机'}>
              {s.power?<StopRounded/>:<PowerSettingsNewRounded/>}
            </button>}
          </div>
        </div>
      </section>
      <section className="au-periph-card">
        <div className="au-periph-label">PERIPHERALS</div>
        <div className="au-periph-list">
          <div className="au-periph-slider">
            <div className="au-periph-row">
              <span className="au-periph-name">{s.mode==='favorite'?'最爱模式':'手动风速'}</span>
              <span className="au-periph-val">{(()=>{const v=s.mode==='favorite'?(sliderLevel??s.device_level):null;return v!=null?v:'—'})()} / {levelMax(snap.config.model, snap.config)}</span>
            </div>
            <Slider value={s.mode==='favorite'?(sliderLevel??s.device_level??0):0} min={0} max={levelMax(snap.config.model, snap.config)}
              disabled={busy||s.power===false||s.linked||s.mode!=='favorite'} valueLabelDisplay="auto"
              onChange={(_,v)=>setSliderLevel(v as number)}
              onChangeCommitted={(_,v)=>setDevice({level:v as number})}/>
          </div>
          <div className="au-periph-blocks">
            <button className={`au-periph-block ${s.led==='on'?'on':''}`} disabled={busy||s.power===false||s.linked} onClick={()=>setDevice({led:s.led!=='on'})}>
              <AirRounded fontSize="small"/>LED
            </button>
            <button className={`au-periph-block ${s.buzzer==='on'?'on':''}`} disabled={busy||s.power===false||s.linked} onClick={()=>setDevice({buzzer:s.buzzer!=='on'})}>
              <NotificationsNoneRounded fontSize="small"/>BEEP
            </button>
            <button className={`au-periph-block ${s.child_lock==='on'?'on':''}`} disabled={busy||s.power===false||s.linked} onClick={()=>setDevice({child_lock:s.child_lock!=='on'})}>
              <LockRounded fontSize="small"/>LOCK
            </button>
          </div>
        </div>
      </section>
    </div>{/* /home-col-left */}

    {/* === 右栏：CHART + SYS MONITOR + OP MODE === */}
    <div className="home-col-right">
      <section className="chart-panel">
        <div className="panel-heading">
          <div><h3>温度趋势</h3><p>{snap.history.length>1?`最近 ${Math.max(.1,(snap.history[snap.history.length-1].t-snap.history[0].t)/60).toFixed(1)} 分钟`:'等待采样'} · CPU Core</p></div>
          <span className="chart-key"><i/> 温度 °C</span>
        </div>
        <TemperatureChart history={snap.history} thresholds={snap.config.control_mode==='curve'?[]:snap.config.thresholds_c} pollSeconds={snap.config.poll_seconds}/>
      </section>
      <section className="sys-monitor">
        <div className="sys-monitor-grid">
          <div className="sys-block">
            <div className="sys-block-title"><ThermostatRounded fontSize="inherit"/> TEMPERATURE</div>
            <div className="sys-row"><span>CPU 温度</span><b>{s.temp?.toFixed(1)??'—'}°C</b></div>
            {s.temp_dec!=null&&<div className="sys-row"><span>设备温度</span><b>{(s.temp_dec/10).toFixed(1)}°C</b></div>}
            <div className="sys-row sys-row-narrow"><span>更新时间</span><b>{s.updated?s.updated:s.sensor||'—'}</b></div>
          </div>
          <div className="sys-block">
            <div className="sys-block-title"><SpeedRounded fontSize="inherit"/> FAN</div>
            <div className="sys-row"><span>风扇转速</span><b>{s.rpm??'—'} RPM</b></div>
            <div className="sys-row"><span>运行模式</span><b>{s.mode?modeName(s.mode):'IDLE'}</b></div>
            <div className="sys-row"><span>目标档位</span><b>{s.target??'—'} / {levelMax(snap.config.model, snap.config)}</b></div>
          </div>
          <div className="sys-block">
            <div className="sys-block-title"><AirRounded fontSize="inherit"/> AIR</div>
            <div className="sys-row"><span>PM2.5</span><b>{s.pm25!=null?s.pm25:'—'} μg/m³</b></div>
            {s.humidity!=null&&<div className="sys-row"><span>湿度</span><b>{s.humidity} %</b></div>}
            <div className="sys-row"><span>滤芯寿命</span><b>{s.filter_life!=null?s.filter_life:'—'}%</b></div>
            {s.filter_days!=null&&<div className="sys-row"><span>剩余天数</span><b>{s.filter_days} DAYS</b></div>}
          </div>
        </div>
      </section>
      <section className="op-mode-panel">
        <div className="op-mode-label">OPERATING MODE</div>
        <div className="op-mode-row op-mode-auc" role="group" aria-label="运行模式">
          <button className={`op-mode-item ${s.mode==='auto'?'active':''}`} disabled={busy||s.power===false||s.linked}
            onClick={()=>action('mode_auto')}><TuneIcon/><span className="op-en">AUTO</span><span className="op-zh">自动</span></button>
          <button className={`op-mode-item ${s.mode==='silent'?'active':''}`} disabled={busy||s.power===false||s.linked}
            onClick={()=>action('mode_silent')}><NightlightRounded/><span className="op-en">SILENT</span><span className="op-zh">睡眠</span></button>
          <button className={`op-mode-item ${s.mode==='favorite'?'active':''}`} disabled={busy||s.power===false||s.linked}
            onClick={()=>action('mode_favorite')}><FavoriteRounded/><span className="op-en">FAVORITE</span><span className="op-zh">最爱</span></button>
          <button className={`op-mode-item ${s.linked?'active linked':''}`} disabled={busy||s.power===false}
            onClick={()=>action(s.linked?'stop':'start')} title="温度联动">
            <ThermostatAutoRounded/><span className="op-en">温控</span><span className="op-zh">CPU LINK</span>
            {s.linked&&<span className="op-badge">● ACTIVE</span>}
          </button>
          <button className="op-mode-item" onClick={()=>setPage('rules')} title="温控模式自定义">
            <BuildRounded/><span className="op-en">自定义</span><span className="op-zh">RULES</span>
          </button>
        </div>
      </section>
    </div>{/* /home-col-right */}
   </div>)}</div>}
   {page==='rules'&&<div className="page-enter"><div className="page-toolbar"><div className="rule-mode-switch" role="group" aria-label="联动模式"><Button variant={draft.control_mode==='bands'?'contained':'outlined'} disabled={locked} onClick={()=>update('control_mode','bands')}>三温区</Button><Button variant={draft.control_mode==='curve'?'contained':'outlined'} disabled={locked} onClick={()=>update('control_mode','curve')}>自定义曲线</Button></div><Button variant={s.linked?'outlined':'contained'} disabled={busy||(dirty&&!s.linked)||((s.temp==null||!deviceReady)&&!s.linked)} startIcon={s.linked?<StopRounded/>:<PlayArrowRounded/>} onClick={()=>action(s.linked?'stop':'start')}>{s.linked?'停止联动':'开启联动'}</Button></div>
    {draft.control_mode==='curve'?<CurveEditor points={draft.curve_points} locked={locked} model={draft.model} onChange={points=>update('curve_points',points)}/>:<div className="rule-cards">{['低温运行','温暖运行','高温运行'].map((label,i)=><section className="panel rule-card" key={i}><span className={`tier tier-${i}`}>0{i+1}</span><h3>{label}</h3><p>{i===0?`低于 ${draft.thresholds_c[0]}°C`:i===1?`${draft.thresholds_c[0]}–${draft.thresholds_c[1]}°C`:`达到 ${draft.thresholds_c[1]}°C`}</p><div className="level-number">{draft.favorite_levels[i]}<span>风量档位</span></div><Slider value={draft.favorite_levels[i]} min={0} max={levelMax(draft.model)} step={1} disabled={locked} aria-label={`${label}风量档位`} valueLabelDisplay="auto" onChange={(_,v)=>{const a=[...draft.favorite_levels];a[i]=v as number;update('favorite_levels',a)}}/><div className="slider-ends"><span>安静 · 0</span><span>强劲 · {levelMax(draft.model)}</span></div></section>)}</div>}
    <section className="panel"><div className="panel-heading"><div><h3>切换条件</h3><p>回差与延时能够减少短时温度波动导致的频繁切换</p></div></div><div className="form-grid">{draft.control_mode==='bands'&&['中温档起点（°C）','高温档起点（°C）'].map((label,i)=><TextField key={label} label={label} type="number" disabled={locked} value={draft.thresholds_c[i]} onChange={e=>{const a=[...draft.thresholds_c];a[i]=Number(e.target.value);update('thresholds_c',a)}}/>)}{field('降温回差（°C）','hysteresis_c','number')}{field('稳定延时（秒）','hold_seconds','number')}{field('采样间隔（秒）','poll_seconds','number')}</div><div className="info-line"><InfoOutlined fontSize="small"/>{draft.control_mode==='curve'?`降档需要低于对应曲线换档温度减去 ${draft.hysteresis_c}°C；目标档位持续 ${draft.hold_seconds} 秒后应用。`:`降温低于 ${draft.thresholds_c[1]-draft.hysteresis_c}°C / ${draft.thresholds_c[0]-draft.hysteresis_c}°C 才降档；目标档位持续 ${draft.hold_seconds} 秒后应用。`}</div></section>{saveFooter}</div>}
   {page==='device'&&<div className="page-enter device-page">{!deviceReady&&<Alert severity="info" sx={{mb:2}}>首次使用：点击下方「自动提取 IP/Token」，Mi Fan 会在弹出的窗口里引导你登录米家账号，并自动把净化器信息填入表单。CPU 温度会独立监测。</Alert>}<div className="device-layout"><div className="device-col-main"><section className="panel panel-hero device-connect-panel"><div className="panel-heading"><div><h3>米家空气净化器</h3><p>通过同一局域网直接连接</p></div><Chip label={connectionGood?'已连接':'未连接'} size="small" color={connectionGood?'success':'default'}/></div><div className="form-grid">{field('设备 IP 地址','ip')}{field('设备型号','model')}{field('设备 Token','token','password',draft.has_token?'已保存 Token，留空表示保留':'填写 32 位设备 Token')}</div><div className="inline-actions">{(()=>{const ex=snap?.extractor;if(ex?.status==='running'){return<><Button variant="contained" disabled={working} onClick={()=>action('cancel_extract')} startIcon={<StopRounded/>}>取消提取</Button><Chip label="提取中…" color="warning" size="small" sx={{ml:1}}/></>;}return<><Button variant="contained" disabled={busy} onClick={()=>action('auto_extract')} startIcon={<RefreshRounded/>}>自动提取 IP / Token</Button><Button variant="outlined" disabled={busy} onClick={()=>action('extract_token')}>手动打开工具</Button></>})()}<Button variant="contained" disabled={busy||!deviceReady||dirty} onClick={()=>action('on')} startIcon={<PowerSettingsNewRounded/>}>净化器开机</Button><Button variant="outlined" disabled={busy||!deviceReady||dirty} onClick={()=>action('off')}>净化器关机</Button></div>{snap?.extractor && snap.extractor.status!=='idle' && snap.extractor.message && <Alert severity={snap.extractor.status==='success'?'success':snap.extractor.status==='running'?'info':'warning'} sx={{mt:2,py:1}} onClose={()=>{}}>{snap.extractor.message}{snap.extractor.status==='success' && draft && <Button size="small" sx={{ml:1}} onClick={()=>action('start')}>开启联动</Button>}</Alert>}</section></div><div className="device-col-side"><section className="panel panel-compact temp-source-panel"><div className="panel-heading"><div><h3>温度数据源</h3><p>Libre Hardware Monitor</p></div><Button onClick={()=>action('reconnect')} disabled={busy} startIcon={<RefreshRounded/>}>重连温度</Button></div><div className="form-grid">{field('温度接口地址','lhm_url')}{field('传感器名称','sensor_name','text','auto 自动识别 CPU 温度；识别失败时填写 Libre 中的准确名称')}</div><div className="file-field">{field('Libre 程序路径','lhm_path')}<Tooltip title="选择 Libre 程序"><IconButton disabled={locked} aria-label="选择 Libre 程序" onClick={async()=>{try{const p=await window.pywebview!.api.choose_lhm();if(p)update('lhm_path',p)}catch(e){pushNotice(String(e),true)}}}><FolderOpenRounded/></IconButton></Tooltip></div><SettingRow title="自动启动 Libre" description="需要时自动启动，关闭 Mi Fan 时一并退出。已自行运行的 Libre 会复用并保留。"><Switch checked={draft.auto_start_lhm} disabled={locked} onChange={(_,v)=>update('auto_start_lhm',v)} inputProps={{'aria-label':'自动启动 Libre'}}/></SettingRow><div className="device-side-footer">{saveFooter}</div></section></div></div></div>}
   {page==='logs'&&<div className="page-enter"><div className="page-toolbar"><span className="muted">最近 {snap.logs.length} 条记录 · 自动更新</span><Select size="small" value={filter} onChange={e=>setFilter(e.target.value)}><MenuItem value="user">用户关心</MenuItem><MenuItem value="all">全部记录</MenuItem><MenuItem value="error">仅错误</MenuItem></Select></div><section className="panel logs-panel">{snap.logs.filter(l=>{if(filter==='error')return l.level==='error';if(filter==='user'){const text=l.text||'';const devNoise=['托盘','pystray','初始化托盘','图标候选','图标导入','实时温度独立监测','已就绪','导入成功'];if(devNoise.some(k=>text.includes(k)))return false;return true}return true}).slice().reverse().map(log=><div className="log-row" key={log.id}><time>{log.time}</time><span className={`log-badge ${log.level}`}>{log.level==='error'?'错误':'信息'}</span><span>{log.text}</span></div>)}{!snap.logs.filter(l=>{if(filter==='error')return l.level==='error';if(filter==='user'){const text=l.text||'';const devNoise=['托盘','pystray','初始化托盘','图标候选','图标导入','实时温度独立监测','已就绪','导入成功'];if(devNoise.some(k=>text.includes(k)))return false;return true}return true}).length&&<div className="empty"><CheckCircleRounded/><h3>暂无记录</h3><p>{filter==='user'?'净化器与温度检测事件会显示在这里':'连接与运行事件会显示在这里'}</p></div>}</section></div>}
   {page==='settings'&&<div className="page-enter"><section className="panel"><h3>外观</h3><SettingRow title="主题模式" description="原生 WebView2 渲染，适配高 DPI 屏幕。"><Select size="small" disabled={locked} value={draft.theme} onChange={e=>update('theme',e.target.value)}><MenuItem value="system">跟随系统</MenuItem><MenuItem value="light">浅色</MenuItem><MenuItem value="dark">深色</MenuItem></Select></SettingRow></section><section className="panel"><h3>运行行为</h3><SettingRow title="开机自启动" description="Windows 启动时自动运行 Mi Fan（通过注册表 Run 项实现）。"><Switch checked={!!draft.autostart} disabled={locked} onChange={async(_,v)=>{update('autostart',v);try{await window.pywebview!.api.toggle_autostart(v);pushNotice(v?'已开启开机自启':'已关闭开机自启')}catch(e){pushNotice(String(e),true)}}} inputProps={{'aria-label':'开机自启动'}}/></SettingRow><SettingRow title="关闭窗口最小化到托盘" description="点击关闭按钮时隐藏窗口并留在系统托盘；双击托盘图标或右键「显示」恢复。"><Switch checked={!!draft.tray_minimize} disabled={locked} onChange={async(_,v)=>{update('tray_minimize',v);try{await window.pywebview!.api.toggle_tray_minimize(v);pushNotice(v?'已开启托盘常驻':'已关闭托盘常驻')}catch(e){pushNotice(String(e),true)}}} inputProps={{'aria-label':'最小化到托盘'}}/></SettingRow><SettingRow title="停止时恢复自动模式" description="停止联动或退出软件时，将净化器切回自动模式。"><Switch checked={draft.restore_auto_on_exit} disabled={locked} onChange={(_,v)=>update('restore_auto_on_exit',v)} inputProps={{'aria-label':'停止时恢复自动模式'}}/></SettingRow><div className="info-line"><InfoOutlined fontSize="small"/> 温度中断时始终尝试恢复自动模式。关机按钮会直接关闭净化器。</div></section><UpdatePanel/><section className="panel about"><div className="brand-icon"><AirRounded/></div><div><h3>Mi Fan <span className="muted">米饭</span></h3><p>让设备协同，让空气流动。</p><small>React · Material UI · WebView2 · Python</small></div></section>{saveFooter}</div>}
   </>}
   </main><footer><span><i className={s?.temp!=null?'online':''}/>{(()=>{const sen=s?.sensor;if(!sen)return'连接中…';if(sen==='正在连接'||sen.includes('正在'))return'启动中';return sen})()}</span><span>{s?.linked?'联动正在运行':'本地监测'}<span className="footer-dot">·</span>{deviceReady?(snap?.human_model||snap?.config?.model||'Mi Air Purifier'):'未连接设备'}</span></footer>
  </div></div>
  <Snackbar key={notice?.key} open={Boolean(notice)} autoHideDuration={4000} onClose={()=>{setNotice(null);setTimeout(showNext,150)}} anchorOrigin={{vertical:'top',horizontal:'right'}} sx={{zIndex:9999}}><Alert variant="filled" severity={notice?.error?'error':'success'} onClose={()=>{setNotice(null);setTimeout(showNext,150)}}>{notice?.text}</Alert></Snackbar>
 </ThemeProvider>
}

function Metric({icon,label,value,unit,note}:{icon:React.ReactNode;label:string;value:string|number;unit:string;note:string}){return <section className="panel metric"><div className="metric-label">{icon}<span>{label}</span></div><div className="metric-value">{value}<span>{unit}</span></div><small>{note}</small></section>}
function StatusRow({label,value,sub}:{label:string;value:string;sub?:string}){return <div className="status-row"><span className="status-row-label">{label}</span><span className="status-row-value">{value}{sub&&<span className="status-row-sub">{sub}</span>}</span></div>}
function SettingRow({title,description,children}:{title:string;description:string;children:React.ReactNode}){return <div className="setting-row"><div><b>{title}</b><p>{description}</p></div>{children}</div>}
function UpdatePanel(){
 const [ver,setVer]=useState<any>(null); const [checking,setChecking]=useState(false); const [result,setResult]=useState<any>(null);
 useEffect(()=>{(window.pywebview?.api as any)?.get_version?.().then(setVer).catch(()=>{});},[]);
 const check=async()=>{
  setChecking(true); setResult(null);
  try{const r=await (window.pywebview!.api as any).check_update(); setResult(r);}
  catch(e){setResult({ok:false,error:String(e)});}
  finally{setChecking(false);}
 };
 return <section className="panel"><h3>软件更新</h3>
  <div className="version-row">
   <span className="version-label">当前版本</span>
   <span className="version-num">{ver?.version||'—'}</span>
  </div>
  <div className="update-row">
   <Button variant="outlined" onClick={check} disabled={checking} startIcon={checking?<CircularProgress size={16}/>:<RefreshRounded/>}>
    {checking?'检查中…':'立即检查更新'}
   </Button>
  </div>
  {result && (
   <div className="update-result">
    {!result.ok ? (
     <Alert severity="warning" sx={{py:1}}>无法连接更新服务器：{result.error}</Alert>
    ) : result.has_update ? (
     <Alert severity="info" sx={{py:1.5}}>
      <b>发现新版本 v{result.latest}</b> （当前 v{result.current}）
      {result.notes && <div className="update-notes">{result.notes.split('\\n').map((l:string,i:number)=><div key={i}>{l}</div>)}</div>}
      {result.download_url && (
       <Button variant="contained" size="small" sx={{mt:1}} onClick={()=>window.open(result.download_url,'_blank')}>前往下载页</Button>
      )}
     </Alert>
    ) : (
     <Alert severity="success" sx={{py:1}}>已经是最新版本 v{result.current} ✅</Alert>
    )}
   </div>
  )}
 </section>;
}
createRoot(document.getElementById('root')!).render(<App/>);

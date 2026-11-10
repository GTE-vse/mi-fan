import fs from 'node:fs';
import assert from 'node:assert/strict';
import {JSDOM,VirtualConsole} from 'jsdom';
import {within,fireEvent,waitFor} from '@testing-library/dom';
const vc=new VirtualConsole();
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window;
w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
w.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
const config={ip:'',model:'zhimi.airpurifier.m1',has_token:false,sensor_name:'auto',lhm_url:'http://127.0.0.1:8085/data.json',lhm_path:'LibreHardwareMonitor/LibreHardwareMonitor.exe',thresholds_c:[65,75],favorite_levels:[0,1,2],hysteresis_c:3,hold_seconds:15,poll_seconds:3,auto_start_lhm:true,restore_auto_on_exit:true,theme:'light',control_mode:'bands',curve_points:[{temp_c:40,level:0},{temp_c:85,level:3}]};
const state={temp:48,target:0,power:null,mode:null,rpm:null,device_level:null,device_connected:false,linked:false,sensor:'监测中',detail:'',updated:'12:00:00',error:null};
const actions=[];
w.pywebview={api:{snapshot:async()=>structuredClone({config,state,version:'3.1.2',busy:false,closing:false,history:[],logs:[]}),
 action:async name=>{actions.push(name);return {ok:true}},
 save_settings:async values=>{Object.assign(config,values);config.has_token=Boolean(values.token);delete config.token;return {ok:true}},
 ui_ready:async()=>true}};
w.eval(fs.readFileSync('dist/assets/app.js','utf8'));
const ui=within(w.document.body);
const until=fn=>waitFor(fn,{container:w.document.body,timeout:4000});
await until(()=>assert.ok(ui.getByText(/首次使用：/)));
assert.equal(ui.getByLabelText('设备 IP 地址').value,'');
assert.ok(ui.getByRole('button',{name:'净化器开机',exact:true}).disabled);
fireEvent.click(ui.getByRole('button',{name:'打开 IP / Token 提取工具'}));
await until(()=>assert.deepEqual(actions,['extract_token']));
await until(()=>assert.equal(ui.getByLabelText('设备 IP 地址').disabled,false));
fireEvent.change(ui.getByLabelText('设备 IP 地址'),{target:{value:'192.0.2.42'}});
await until(()=>assert.equal(ui.getByRole('button',{name:'保存设置'}).disabled,false));
fireEvent.change(ui.getByLabelText('设备 Token'),{target:{value:'a'.repeat(32)}});
await until(()=>assert.equal(ui.getByLabelText('设备 Token').value,'a'.repeat(32)));
fireEvent.click(ui.getByRole('button',{name:'保存设置'}));
await until(()=>assert.ok(!ui.queryByText(/首次使用：/)));
assert.equal(ui.getByLabelText('设备 Token').value,'');
await until(()=>assert.ok(!ui.getByRole('button',{name:'净化器开机',exact:true}).disabled));
fireEvent.click(ui.getByRole('button',{name:'概览',exact:true}));
await until(()=>assert.ok(ui.getByTestId('dashboard')));
assert.equal(actions.includes('start'),false);
console.log('PASS: fresh device setup, explicit extractor launch, private token save, and no automatic device commands.');
dom.window.close();

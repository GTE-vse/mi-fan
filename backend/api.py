import ctypes
import copy
from collections import deque
from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import winreg
from urllib.parse import urlparse

from controller import select_band, level_max, resolve_level_max
from rule_engine import DEFAULT_CURVE, curve_preview, select_target

# 米家空气净化器型号 → 中文名映射（覆盖主流型号）
_MODEL_NAMES = {
    'zhimi.airpurifier.m1':    '米家空气净化器 1代',
    'zhimi.airpurifier.m2':    '米家空气净化器 2代',
    'zhimi.airpurifier.ma2':   '米家空气净化器 2S',
    'zhimi.airpurifier.sa2':   '米家空气净化器 Pro H',
    'zhimi.airpurifier.mc2':   '米家空气净化器 H',
    'zhimi.airpurifier.v3':    '米家空气净化器 Pro',
    'zhimi.airpurifier.v5':    '米家空气净化器 Pro',
    'zhimi.airpurifier.v6':    '米家空气净化器 Pro 2',
    'zhimi.airpurifier.v7':    '米家空气净化器 Pro H',
    'zhimi.airpurifier.pro':   '米家空气净化器 Pro',
    'zhimi.airpurifier.pro2':  '米家空气净化器 Pro 2',
    'zhimi.airpurifier.proh':  '米家空气净化器 Pro H',
    'zhimi.airpurifier.4':     '米家空气净化器 4',
    'zhimi.airpurifier.4pro':  '米家空气净化器 4 Pro',
    'zhimi.airpurifier.4lite': '米家空气净化器 4 Lite',
    'zhimi.airpurifier.4h':    '米家空气净化器 4 H',
    'zhimi.airpurifier.f4':    '米家空气净化器 F4',
    'zhimi.airpurifier.a1':    '米家空气净化器 A1',
    'zhimi.airpurifier.mini':  '米家空气净化器 Mini',
    'zhimi.airfresh.a1':       '米家新风机 A1',
    'zhimi.airfresh.t200':     '米家新风机 200',
    'zhimi.airfresh.t300':     '米家新风机 300',
}

def _human_model(model):
    return _MODEL_NAMES.get(model, model or 'Mi Air Purifier')
from link_logic import validate, run_worker
from services import DeviceHub, LiveMonitor


def _run_powershell(script):
    """Run a PowerShell CIM query and return stdout stripped, or '' on failure."""
    try:
        res = subprocess.run(
            ['powershell','-NoProfile','-NonInteractive','-Command',script],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return res.stdout.strip()
    except Exception:
        return ''

def _hardware_info():
    """Read CPU/GPU/DRAM via registry + PowerShell CIM (Win11 24H2 has no wmic)."""
    import winreg
    def _reg(hive, path, name):
        try:
            with winreg.OpenKey(hive, path) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None
    cpu = _reg(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or ""
    # GPU: PowerShell CIM — 优先独显 (NVIDIA/AMD/Radeon/GeForce/RTX)，跳过核显 (Intel/UHD/Iris/Basic)
    gpu = ""
    try:
        raw = _run_powershell(
            "$all = Get-CimInstance Win32_VideoController; "
            "$preferred = $all | Where-Object {$_.Name -match 'NVIDIA|AMD|Radeon|GeForce|RTX|RX|GTX|Arc'} | Select-Object -First 1 -ExpandProperty Name; "
            "if (-not $preferred) { $preferred = $all | Where-Object {$_.Name -notmatch 'Intel|UHD|Iris|Basic'} | Select-Object -First 1 -ExpandProperty Name }; "
            "if (-not $preferred) { $preferred = $all | Select-Object -First 1 -ExpandProperty Name }; "
            "Write-Output $preferred")
        gpu = raw or ""
    except Exception:
        pass
    # DRAM: PowerShell CIM
    dram = ""
    try:
        raw = _run_powershell(
            "$chips = Get-CimInstance Win32_PhysicalMemory; "
            "$totalGB = [math]::Round((($chips | Measure-Object Capacity -Sum).Sum / 1GB), 0); "
            "$count = $chips.Count; "
            "$speed = $chips[0].Speed; "
            "Write-Output \"${totalGB}GB x${count} ${speed}MHz\"")
        dram = raw or ""
        # 一些 BIOS 报告 0MHz，去掉尾巴
        if dram.endswith(' 0MHz'):
            dram = dram[:-5]
    except Exception:
        pass
    import os
    hostname = os.environ.get('COMPUTERNAME','')
    return dict(cpu=cpu, gpu=gpu, dram=dram, hostname=hostname)


HW_INFO = _hardware_info()  # cached once at import time


class Api:
    def __init__(self, app_dir, monitoring=True, hub=None):
        self.app_dir=Path(app_dir)
        self.path=self.app_dir/'config.json'
        self.config=dict(ip='',token='',model='zhimi.airpurifier.ma2',
            thresholds_c=[65,75],favorite_levels=[0,1,2],hysteresis_c=3,
            poll_seconds=3,hold_seconds=15,restore_auto_on_exit=True,auto_start_lhm=True,
            sensor_name='auto',lhm_url='http://127.0.0.1:8085/data.json',
            lhm_path=str(self.app_dir/'LibreHardwareMonitor/LibreHardwareMonitor.exe'),theme='system',
            control_mode='bands',curve_points=copy.deepcopy(DEFAULT_CURVE),
            autostart=False,tray_minimize=False)
        self.lock=threading.RLock()
        self.operations=threading.Lock()
        self.events=queue.Queue()
        self.stop=threading.Event()
        self.shutdown_event=threading.Event()
        self.worker=None
        self.generation=0
        self.window=None
        self.busy=False
        self.closing=False
        self._tray_started=False
        self._tray_icon=None
        self.history=deque(maxlen=601)
        self.logs=deque(maxlen=400)
        self.sequence=0
        self.state=dict(temp=None,power=None,mode=None,rpm=None,device_level=None,
                        target=None,sensor='正在连接',linked=False,detail='温度监测会自动启动',
                        device_connected=False,updated=None,error=None,
                        pm25=None,humidity=None,filter_life=None,filter_days=None,
                        led=None,buzzer=None,child_lock=None,temp_dec=None,speed=None)
        try:
            if self.path.exists(): self.config.update(json.loads(self.path.read_text(encoding='utf-8-sig')))
        except Exception:
            self.log('配置文件无法读取，请在设备页面重新填写。','error')
        bundled=self.app_dir/'LibreHardwareMonitor/LibreHardwareMonitor.exe'
        configured=Path(self.config.get('lhm_path',''))
        if not configured.is_absolute():
            self.config['lhm_path']=str(self.app_dir/configured)
        if not Path(self.config.get('lhm_path','')).is_file() and bundled.is_file():
            self.config['lhm_path']=str(bundled)
        self.hub=hub or DeviceHub()
        self.monitor=LiveMonitor(self.config,self.events,self.hub)
        self.extractor=dict(running=False,status='idle',result=None,message='')
        self.extractor_process=None
        self.extractor_output=None
        if monitoring:
            threading.Thread(target=self.consume,daemon=True).start()
            self.monitor.start()
        self.log('Mi Fan 已就绪，实时温度独立监测。')

    def log(self,text,level='info'):
        with self.lock:
            token=self.config.get('token','')
            if token: text=str(text).replace(token,'[已隐藏]')
            self.sequence+=1
            self.logs.append(dict(id=self.sequence,time=datetime.now().strftime('%H:%M:%S'),level=level,text=text))

    def consume(self):
        while not self.shutdown_event.is_set():
            try: event=self.events.get(timeout=0.3)
            except queue.Empty: continue
            self.consume_event(event)

    def consume_event(self,event):
        with self.lock:
            kind=event[0]
            if kind=='link':
                if event[1]==self.generation: self.consume_event(event[2])
                return
            if kind=='temperature':
                self.state['temp']=event[1]
                now=time.time()
                self.history.append(dict(t=now,v=event[1]))
                while self.history and self.history[0]['t'] < now-600:
                    self.history.popleft()
                if event[1] is not None:
                    self.state['updated']=datetime.now().strftime('%H:%M:%S')
                    if not self.state['linked']:
                        self.state['target']=select_target(event[1],None,self.config)[1]
                elif not self.state['linked']:
                    self.state['target']=None
            elif kind=='sensor_status': self.state['sensor']=event[1]
            elif kind=='device_status':
                result=event[1]
                self.state['device_connected']=result is not None
                if result:
                    # 第一次连接成功且没有探测过档位上限 → 后台自动探测
                    if not self.config.get('level_max_probed'):
                        threading.Thread(target=self._auto_probe_level_max,daemon=True).start()
                    # result is a dict from DeviceHub.status(); also accept legacy list for safety.
                    if isinstance(result, dict):
                        self.state.update(
                            power=result.get('power')=='on',
                            mode=result.get('mode'),
                            device_level=result.get('favorite_level'),
                            rpm=result.get('motor1_speed') or result.get('speed'),
                            pm25=result.get('pm2_5'),
                            humidity=result.get('humidity'),
                            temp_dec=result.get('temp_dec'),
                            filter_life=result.get('filter_life'),
                            filter_days=result.get('filter_left_time'),
                            led=result.get('led'),
                            buzzer=result.get('buzzer'),
                            child_lock=result.get('child_lock'),
                            speed=result.get('speed'),
                        )
                    elif isinstance(result, list) and len(result)>=4:
                        self.state.update(power=result[0]=='on',mode=result[1],device_level=result[2],rpm=result[3])
                else:
                    self.state.update(power=None,mode=None,device_level=None,rpm=None,
                        pm25=None,humidity=None,filter_life=None,filter_days=None,
                        led=None,buzzer=None,child_lock=None,temp_dec=None,speed=None)
            elif kind=='log':
                failed=any(s in event[1] for s in ('失败','不可用','运行已停止：'))
                self.log(event[1],'error' if failed else 'info')
                if failed: self.state['error']=event[1]
            elif kind=='status': self.state['detail']=event[1]
            elif kind=='reading': self.state.update(target=event[2] if isinstance(event[2],int) else None,detail=event[3])
            elif kind=='done':
                self.state.update(linked=False,detail='联动已停止，温度监测继续运行')

    def _auto_probe_level_max(self):
        """后台线程：探测设备实际支持的档位上限，存 config 缓存。"""
        try:
            self.log('正在自动探测设备档位上限…')
            cfg=dict(self.config)
            found=self.hub.probe_level_max(cfg)
            self.config['level_max']=found
            self.config['level_max_probed']=True
            self._save_config()
            self.log('设备档位上限探测完成：%d 档。'%found)
        except Exception as exc:
            self.log('自动探测失败：%s（将使用默认值 16 档）'%exc)

    def _save_config(self):
        """原子写入 config.json（从 save_settings 抽出来共享）。"""
        try:
            temporary=self.path.with_suffix('.json.tmp')
            saved=copy.deepcopy(self.config)
            if Path(saved.get('lhm_path','')).resolve()==(self.app_dir/'LibreHardwareMonitor/LibreHardwareMonitor.exe').resolve():
                saved['lhm_path']='LibreHardwareMonitor/LibreHardwareMonitor.exe'
            temporary.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.path)
        except Exception as e:
            self.log('保存 config 失败：%s'%e)

    def toggle_autostart(self,enable):
        """开机自启：写/删 Windows 注册表 Run 项。"""
        key_path=r'Software\Microsoft\Windows\CurrentVersion\Run'
        app_name='MiFanDesktop'
        exe=str(self.app_dir/'mi_fan.exe')
        key=winreg.OpenKey(winreg.HKEY_CURRENT_USER,key_path,0,winreg.KEY_SET_VALUE|winreg.KEY_READ)
        try:
            if enable:
                winreg.SetValueEx(key,app_name,0,winreg.REG_SZ,f'"{exe}" --silent')
            else:
                try: winreg.DeleteValue(key,app_name)
                except FileNotFoundError: pass
            self.config['autostart']=bool(enable)
            self._save_config()
        finally:
            winreg.CloseKey(key)
        return self.config['autostart']

    def toggle_tray_minimize(self,enable):
        """最小化到托盘偏好。"""
        self.config['tray_minimize']=bool(enable)
        self._save_config()
        return self.config['tray_minimize']

    def get_tray_status(self):
        return {'autostart':bool(self.config.get('autostart')),'tray_minimize':bool(self.config.get('tray_minimize'))}

    def show_window(self):
        hwnd=self._hwnd()
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd,9)  # SW_RESTORE
                ctypes.windll.user32.ShowWindow(hwnd,5)  # SW_SHOW
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                ctypes.windll.user32.BringWindowToTop(hwnd)
                return
            except Exception: pass
        if self.window:
            try: self.window.restore()
            except: pass
            try: self.window.show()
            except: pass
            try: self.window.focus()
            except: pass

    def hide_window(self):
        hwnd=self._hwnd()
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd,0)  # SW_HIDE
                return
            except Exception: pass
        if self.window:
            try: self.window.hide()
            except: pass

    def _hwnd(self):
        try:
            hwnd=ctypes.windll.user32.FindWindowW(None,'Mi Fan · 米饭 — 小米空气净化器控制器')
            if hwnd: return hwnd
            return self.window._hwnd if self.window else None
        except: return None

    def handle_minimize_to_tray(self):
        """closing 事件调用。返回 True=拦截关闭 hide 到托盘；返回 False=正常关闭。"""
        if not self.config.get('tray_minimize'):
            return False
        if not getattr(self,'_tray_started',False):
            ok=self.init_tray()
            if not ok:
                self.log('托盘启动失败，改为正常关闭')
                return False
        return True

    def init_tray(self):
        """用 pystray 启动系统托盘。返回 True=成功。"""
        if getattr(self,'_tray_started',False):
            return True
        self.log('正在初始化托盘图标…')
        try:
            import pystray
            from pystray import Icon, Menu, MenuItem
            from PIL import Image, ImageDraw
            self.log('pystray/Pillow 导入成功')
        except ImportError as exc:
            self.log('pystray/Pillow 不可用：%s（托盘功能需打包时 collect-submodules pystray PIL）'%exc,'error')
            return False
        try:
            # 图标可能在三个位置：exe 旁边（app_dir）、PyInstaller _MEIPASS、开发源码根目录
            import sys as _sys
            candidates=[]
            candidates.append(self.app_dir/'mi_fan.ico')
            if hasattr(_sys,'_MEIPASS'):
                candidates.append(Path(_sys._MEIPASS)/'mi_fan.ico')
            candidates.append(Path(__file__).resolve().parent.parent/'mi_fan.ico')
            icon_path=None
            for p in candidates:
                if p.is_file():
                    icon_path=p; break
            self.log('托盘图标候选: %s → 选中: %s'%( [str(c) for c in candidates], icon_path ))
            if icon_path:
                image=Image.open(str(icon_path))
            else:
                self.log('托盘图标文件都没找到，用占位图')
                image=Image.new('RGBA',(64,64),(0,0,0,0))
                d=ImageDraw.Draw(image)
                d.ellipse((8,8,56,56),fill=(138,43,226,255))
                d.ellipse((22,22,42,42),fill=(255,255,255,255))
        except Exception as exc:
            self.log('加载托盘图标失败，使用占位图: %s'%exc)
            image=Image.new('RGBA',(64,64),(0,0,0,0))
            d=ImageDraw.Draw(image)
            d.ellipse((8,8,56,56),fill=(138,43,226,255))
            d.ellipse((22,22,42,42),fill=(255,255,255,255))

        def _on_show(icon,item):
            self.show_window()
        def _on_quit(icon,item):
            try: self.shutdown()
            except: pass
            try: icon.stop()
            except: pass
            self.closing=True
            os._exit(0)

        try:
            tray_icon=Icon('Mi Fan', image, self._tray_title(), self._tray_menu())
            self._tray_icon=tray_icon
            self._tray_started=True
            threading.Thread(target=tray_icon.run,daemon=True).start()
            self.log('托盘图标线程已启动')
            # 定时刷新 tooltip 和菜单（3 秒一次）
            def _refresh_loop():
                while getattr(self,'_tray_started',False):
                    try:
                        tray_icon.title=self._tray_title()
                        tray_icon.menu=self._tray_menu()
                    except Exception: pass
                    time.sleep(3)
            threading.Thread(target=_refresh_loop,daemon=True).start()
            self.log('托盘图标已就绪 ✅')
            return True
        except Exception as exc:
            self.log('托盘创建异常：%s'%exc,'error')
            self._tray_started=False
            self._tray_icon=None
            return False

    def _tray_title(self):
        """托盘 tooltip：一行状态摘要。"""
        try:
            snap=self.snapshot()
            s=snap.get('state',{})
            parts=[]
            if s.get('device_connected'):
                power=s.get('power')
                # power 在 consume_event 里被转成布尔了，但要兼容字符串
                if power is True or power=='on':
                    parts.append('● 运行中')
                else:
                    parts.append('○ 已关机')
                if s.get('temp') is not None:
                    parts.append('%s°C'%s['temp'])
                if s.get('pm25') is not None:
                    parts.append('PM2.5 %s'%s['pm25'])
                if s.get('humidity') is not None:
                    parts.append('%s%%'%s['humidity'])
                if s.get('mode'):
                    parts.append(s['mode'].capitalize())
                if s.get('rpm'):
                    parts.append('%s RPM'%s['rpm'])
            else:
                parts.append('未连接净化器')
            return 'Mi Fan · ' + ' | '.join(parts)
        except Exception:
            return 'Mi Fan · 米饭'

    def _tray_menu(self):
        """托盘右键菜单：动态生成，显示实时状态。"""
        from pystray import Menu, MenuItem
        def _noop(icon,item): pass
        try:
            snap=self.snapshot()
            s=snap.get('state',{})
            items=[]
            if s.get('device_connected'):
                power=s.get('power')
                if power is True or power=='on':
                    items.append(MenuItem('● 净化器运行中', _noop, enabled=False))
                    if s.get('temp') is not None:
                        items.append(MenuItem('  温度: %s°C'%s['temp'], _noop, enabled=False))
                    if s.get('pm25') is not None:
                        items.append(MenuItem('  PM2.5: %s'%s['pm25'], _noop, enabled=False))
                    if s.get('humidity') is not None:
                        items.append(MenuItem('  湿度: %s%%'%s['humidity'], _noop, enabled=False))
                    if s.get('mode'):
                        items.append(MenuItem('  模式: %s'%s['mode'].capitalize(), _noop, enabled=False))
                    if s.get('device_level') is not None:
                        items.append(MenuItem('  档位: %s'%s['device_level'], _noop, enabled=False))
                    if s.get('rpm'):
                        items.append(MenuItem('  转速: %s RPM'%s['rpm'], _noop, enabled=False))
                else:
                    items.append(MenuItem('○ 净化器已关机', _noop, enabled=False))
            else:
                items.append(MenuItem('⚠ 未连接净化器', _noop, enabled=False))
            items.append(Menu.SEPARATOR)
            items.append(MenuItem('显示 Mi Fan', lambda i,it: self.show_window(), default=True))
            items.append(Menu.SEPARATOR)
            items.append(MenuItem('退出 Mi Fan', self._tray_quit))
            return Menu(*items)
        except Exception:
            return Menu(
                MenuItem('显示 Mi Fan', lambda i,it: self.show_window(), default=True),
                Menu.SEPARATOR,
                MenuItem('退出 Mi Fan', self._tray_quit),
            )

    def _tray_quit(self, icon=None, item=None):
        try: self.shutdown()
        except: pass
        try:
            if hasattr(self,'_tray_icon') and self._tray_icon:
                self._tray_icon.stop()
                self._tray_icon=None
        except: pass
        self.closing=True
        os._exit(0)

    def shutdown_tray(self):
        try:
            if hasattr(self,'_tray_icon') and self._tray_icon:
                self._tray_icon.stop()
                self._tray_icon=None
        except Exception: pass
        self._tray_started=False

    def snapshot(self):
        with self.lock:
            config=copy.deepcopy(self.config)
            config.pop('token',None)
            config['has_token']=bool(self.config.get('token'))
            ex=copy.deepcopy(self.extractor)
            if ex.get('result'):
                ex['result']=dict(ip=ex['result'].get('ip'),model=ex['result'].get('model'))
            return dict(state=copy.deepcopy(self.state),config=config,history=list(self.history),
                        logs=list(self.logs),busy=self.busy,closing=self.closing,version='3.1.2',
                        extractor=ex,hardware=HW_INFO,human_model=_human_model(self.config.get('model','')))

    def checked_config(self,values):
        current=copy.deepcopy(self.config)
        allowed=set(current)-{'token'}
        for key in allowed:
            if key in values: current[key]=values[key]
        if values.get('token'): current['token']=values['token'].strip()
        for key in ('ip','model','sensor_name','lhm_url','lhm_path'):
            current[key]=str(current[key]).strip()
        for key in ('hysteresis_c','hold_seconds','poll_seconds'):
            current[key]=float(current[key])
        if len(current['thresholds_c'])!=2 or len(current['favorite_levels'])!=3:
            raise ValueError('请设置两个温度阈值和三个风量档位。')
        if current.get('theme') not in ('light','dark','system'): raise ValueError('无效主题。')
        for key in ('auto_start_lhm','restore_auto_on_exit'):
            if type(current[key]) is not bool: raise ValueError('开关设置无效。')
        validate(current, require_device=False)
        return current

    def save_settings(self,values):
        if not self.operations.acquire(False): return dict(ok=False,error='操作正在进行，请稍候。')
        try:
            with self.lock:
                if self.state['linked']: raise ValueError('请先停止联动，再修改设置。')
                if self.closing: raise ValueError('程序正在退出。')
                updated=self.checked_config(values)
                temporary=self.path.with_suffix('.json.tmp')
                saved=copy.deepcopy(updated)
                if Path(updated['lhm_path']).resolve()==(self.app_dir/'LibreHardwareMonitor/LibreHardwareMonitor.exe').resolve():
                    saved['lhm_path']='LibreHardwareMonitor/LibreHardwareMonitor.exe'
                temporary.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
                temporary.replace(self.path)
                self.config=updated
                self.monitor.update(updated)
                if self.state['temp'] is not None:
                    self.state['target']=select_target(self.state['temp'],None,updated)[1]
            self.log('设置已保存。')
            return dict(ok=True)
        except Exception as exc: return self.failure(exc)
        finally: self.operations.release()

    def failure(self,exc):
        message=str(exc)
        token=self.config.get('token','')
        if token: message=message.replace(token,'[已隐藏]')
        self.log(message,'error')
        with self.lock: self.state['error']=message
        return dict(ok=False,error=message)

    def _stop_link(self):
        self.stop.set()
        worker=self.worker
        if worker and worker.is_alive():
            worker.join(timeout=35)
            if worker.is_alive(): raise RuntimeError('正在等待净化器响应，请稍后重试。')
        # Consume all events before another worker starts to prevent stale done events.
        while True:
            try: self.consume_event(self.events.get_nowait())
            except queue.Empty: break
        self.worker=None
        with self.lock: self.state.update(linked=False,detail='联动已停止，温度监测继续运行')

    # --- IP / Token auto extract ---

    IP_RE=re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b')
    TOKEN_RE=re.compile(r'\b([a-fA-F0-9]{32})\b')

    @classmethod
    def _parse_extract_output(cls, content):
        """Parse token_extractor output (JSON file or raw text). Returns list of device dicts."""
        if not content or not content.strip():
            return []
        devices=[]
        # Try JSON first.
        try:
            data=json.loads(content)
            if isinstance(data, dict):
                candidates=[]
                for k in ('devices','result','data','device_list','list','items'):
                    if k in data and isinstance(data[k], list):
                        candidates=data[k]; break
                if not candidates: candidates=[data]
                for item in candidates:
                    if not isinstance(item, dict): continue
                    ip = item.get('ip') or item.get('ip_address') or item.get('localip') or item.get('local_ip') or ''
                    token = item.get('token') or item.get('device_token') or item.get('devicetoken') or ''
                    model = item.get('model') or item.get('did_type') or item.get('device_model') or ''
                    if not ip:
                        for k,v in item.items():
                            if isinstance(v,str) and cls.IP_RE.search(v):
                                ip=cls.IP_RE.search(v).group(1); break
                    if not token:
                        for k,v in item.items():
                            if isinstance(v,str) and cls.TOKEN_RE.search(v):
                                token=cls.TOKEN_RE.search(v).group(1); break
                    if ip and token and model:
                        devices.append(dict(ip=ip, token=token, model=model))
                    elif ip and token:
                        devices.append(dict(ip=ip, token=token, model=''))
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        ip = item.get('ip') or item.get('ip_address') or ''
                        token = item.get('token') or item.get('device_token') or ''
                        model = item.get('model') or ''
                        if ip and token:
                            devices.append(dict(ip=ip, token=token, model=model))
        except Exception:
            pass
        # Fallback: regex over raw text (e.g. debug log stdout).
        if not devices:
            ips=[m.group(1) for m in cls.IP_RE.finditer(content)]
            tokens=[m.group(1) for m in cls.TOKEN_RE.finditer(content)]
            if ips and tokens:
                # Pair the first IP with the first token.
                devices.append(dict(ip=ips[0], token=tokens[0], model=''))
        return devices

    def _poll_extract_result(self, output_path, process_ref):
        """Background thread: monitor extractor output file, parse, auto-fill config."""
        deadline=time.monotonic()+600  # 10 min safety net
        try:
            while time.monotonic()<deadline:
                if self.shutdown_event.is_set(): break
                with self.lock:
                    if not self.extractor.get('running'): break
                proc=process_ref()
                if proc is not None:
                    ret=proc.poll()
                    if ret is not None and ret!=0:
                        with self.lock:
                            self.extractor['status']='failed'
                            self.extractor['message']='提取工具返回错误码 %d，请在独立窗口中检查。'%ret
                        self.log('Token 提取工具异常退出（代码 %d）。'%ret,'error')
                        break
                op=Path(output_path)
                if op.is_file() and op.stat().st_size>0:
                    try:
                        text=op.read_text(encoding='utf-8-sig',errors='replace')
                        devices=self._parse_extract_output(text)
                    except Exception as e:
                        devices=[]
                    if devices:
                        with self.lock:
                            target=[d for d in devices if d.get('model','').startswith('zhimi.airpurifier')]
                            pick=target[0] if target else devices[0]
                            self.config['ip']=pick['ip']
                            self.config['token']=pick['token']
                            if pick.get('model'): self.config['model']=pick['model']
                            self.extractor['running']=False
                            self.extractor['status']='success'
                            self.extractor['result']=pick
                            self.extractor['message']='已自动识别 %d 个设备，已填入净化器 IP 和 Token。'%len(devices)
                        self.log('自动提取成功：IP=%s，Token=%s'%(pick['ip'],pick['token'][:8]+'…'))
                        try: self.save_settings({})
                        except Exception: pass
                        break
                    elif proc is not None and proc.poll() is not None:
                        # Process exited but no parseable result.
                        with self.lock:
                            self.extractor['running']=False
                            self.extractor['status']='empty'
                            self.extractor['message']='工具已退出，但未能解析出 IP/Token，请在其窗口内检查是否登录成功。'
                        self.log('Token 提取工具已退出，但未能解析结果。','error')
                        break
                time.sleep(1)
            else:
                with self.lock:
                    self.extractor['running']=False
                    self.extractor['status']='timeout'
                    self.extractor['message']='等待超时（10 分钟），请检查工具窗口是否卡住。'
                self.log('Token 提取等待超时。','error')
        finally:
            with self.lock:
                if self.extractor.get('running'):
                    self.extractor['running']=False
                    self.extractor['status']='cancelled'
                    self.extractor['message']='已取消。'

    def action(self,name):
        if not self.operations.acquire(False): return dict(ok=False,error='操作正在进行，请稍候。')
        with self.lock: self.busy=True; self.state['error']=None
        try:
            if self.closing: raise RuntimeError('程序正在退出。')
            config=copy.deepcopy(self.config)
            if name=='start':
                validate(config)
                if self.worker and self.worker.is_alive(): raise RuntimeError('联动已在运行。')
                self._stop_link()
                self.monitor.cached(config)
                self.stop.clear()
                self.generation+=1
                generation=self.generation
                events=self.events
                class LinkEvents:
                    def put(self,event): events.put(('link',generation,event))
                with self.lock: self.state.update(linked=True,detail='正在连接净化器…')
                self.worker=threading.Thread(target=run_worker,args=(config,True,self.stop,LinkEvents()),
                    kwargs={'reader':self.monitor.cached,'factory':lambda *args:self.hub.bound(config)},daemon=True)
                self.worker.start()
            elif name=='stop': self._stop_link()
            elif name in ('on','off'):
                validate(config)
                self._stop_link()
                self.hub.power(config,name=='on')
                with self.lock:
                    self.state.update(power=name=='on',mode='auto' if name=='on' else self.state['mode'],device_connected=True)
                self.log('净化器已'+('开启（自动模式）' if name=='on' else '关闭')+'。')
            elif name=='reconnect': self.monitor.reconnect(); self.log('正在重新连接温度监测。')
            elif name in ('mode_auto','mode_silent','mode_favorite','mode_strong'):
                validate(config, require_device=True)
                mode={'mode_auto':'auto','mode_silent':'silent','mode_favorite':'favorite','mode_strong':'strong'}[name]
                cn={'mode_auto':'自动','mode_silent':'睡眠','mode_favorite':'最爱','mode_strong':'强力'}[name]
                self.hub.set_mode(config,mode)
                with self.lock: self.state.update(mode=mode,device_connected=True)
                self.log('净化器模式切换为：'+cn+'。')
            elif name in ('mode_next',):
                validate(config, require_device=True)
                order=('auto','favorite','silent','strong')
                current=self.state.get('mode') or 'auto'
                try: idx=(order.index(current)+1)%len(order)
                except ValueError: idx=0
                mode=order[idx]
                self.hub.set_mode(config,mode)
                with self.lock: self.state.update(mode=mode,device_connected=True)
                self.log('净化器模式切换为：'+mode+'。')
            elif name=='extract_token':
                tool=self.app_dir/'工具/token_extractor.exe'
                if not tool.is_file(): raise RuntimeError('找不到 Token 提取工具，请完整解压分享包。')
                subprocess.Popen([str(tool)],cwd=str(tool.parent),creationflags=subprocess.CREATE_NEW_CONSOLE)
                self.log('已打开独立提取工具，请在它的窗口内完成登录，获取自己的 IP 和 Token。')
            elif name=='auto_extract':
                tool=self.app_dir/'工具/token_extractor.exe'
                if not tool.is_file(): raise RuntimeError('找不到 Token 提取工具，请完整解压分享包。')
                with self.lock:
                    if self.extractor.get('running'):
                        raise RuntimeError('提取工具已在运行，请稍候。')
                    # Reset state.
                    self.extractor=dict(running=True,status='running',result=None,
                        message='提取工具已启动，请在弹出的窗口里登录并选择米家设备；AirLink 会自动识别结果。')
                op=self.app_dir/'extract_result.json'
                try: op.unlink()
                except Exception: pass
                creation=0x08000000|subprocess.CREATE_NEW_CONSOLE
                proc=subprocess.Popen([str(tool),'-o',str(op),'-l','DEBUG'],
                    cwd=str(tool.parent),creationflags=creation)
                self.extractor_process=proc
                self.extractor_output=str(op)
                self.log('已启动自动提取：%s（PID %d），等待结果写入 %s'%(tool.name,proc.pid,op.name))
                process_holder={'proc':proc}
                threading.Thread(target=self._poll_extract_result,
                    args=(str(op), lambda: process_holder['proc']),daemon=True).start()
            elif name=='cancel_extract':
                with self.lock:
                    self.extractor['running']=False
                    self.extractor['status']='cancelled'
                    self.extractor['message']='已取消。'
                proc=getattr(self,'extractor_process',None)
                if proc is not None:
                    try: proc.kill()
                    except Exception: pass
                    self.extractor_process=None
                self.log('已取消自动提取。')
            else: raise ValueError('不支持的操作。')
            return dict(ok=True)
        except Exception as exc: return self.failure(exc)
        finally:
            with self.lock: self.busy=False
            self.operations.release()

    def choose_lhm(self):
        import webview
        result=self.window.create_file_dialog(webview.OPEN_DIALOG,file_types=('Libre Hardware Monitor (*.exe)',))
        return result[0] if result else None

    def preview_curve(self,points,temperature):
        try:
            cfg=self._snapshot()
            ml=resolve_level_max(cfg.get('model','zhimi.airpurifier.m1'),cfg)
            return dict(ok=True,**curve_preview(points,temperature,max_level=ml))
        except (ValueError,TypeError) as exc: return dict(ok=False,error=str(exc))

    def set_device(self,params):
        """Manual device control: {level:int, mode:str, led:bool, buzzer:bool, child_lock:bool}."""
        try:
            with self.lock:
                if self.closing: raise RuntimeError('程序正在退出。')
                config=copy.deepcopy(self.config)
            validate(config, require_device=True)
            changes=[]
            if 'mode' in params and params['mode']:
                mode=str(params['mode'])
                if mode not in ('auto','favorite','silent','strong'):
                    raise ValueError('无效模式：'+mode)
                self.hub.set_mode(config,mode)
                changes.append('模式='+mode)
                with self.lock: self.state['mode']=mode
            if 'level' in params and params['level'] is not None:
                level=int(params['level'])
                ml=resolve_level_max(config.get('model','zhimi.airpurifier.m1'),config)
                if not (0<=level<=ml):
                    raise ValueError('档位超出范围 0..%d'%ml)
                self.hub.set_level(config,level)
                changes.append('档位='+str(level))
                with self.lock:
                    self.state['device_level']=level
                    self.state['mode']='favorite'
            if 'led' in params:
                self.hub.led(config,bool(params['led']))
                with self.lock: self.state['led']='on' if params['led'] else 'off'
                changes.append('LED='+('on' if params['led'] else 'off'))
            if 'buzzer' in params:
                self.hub.buzzer(config,bool(params['buzzer']))
                with self.lock: self.state['buzzer']='on' if params['buzzer'] else 'off'
                changes.append('蜂鸣='+('on' if params['buzzer'] else 'off'))
            if 'child_lock' in params:
                self.hub.child_lock(config,bool(params['child_lock']))
                with self.lock: self.state['child_lock']='on' if params['child_lock'] else 'off'
                changes.append('童锁='+('on' if params['child_lock'] else 'off'))
            with self.lock: self.state['device_connected']=True
            self.log('手动控制：'+('，'.join(changes) if changes else '无变化。'))
            return dict(ok=True)
        except Exception as exc: return self.failure(exc)

    def shutdown(self):
        with self.lock: self.closing=True
        # Terminate extractor subprocess if still alive.
        with self.lock: self.extractor['running']=False
        proc=getattr(self,'extractor_process',None)
        if proc is not None:
            try: proc.kill()
            except Exception: pass
            self.extractor_process=None
        with self.operations:
            self._stop_link()
            self.monitor.close()
            self.shutdown_event.set()


def safe_close(api,window):
    if api.shutdown_event.is_set(): return True
    with api.lock:
        if api.closing: return False
        api.closing=True
    def finish():
        try: api.shutdown(); window.destroy()
        except Exception as exc:
            api.failure(exc)
            with api.lock: api.closing=False
    threading.Thread(target=finish,daemon=True).start()
    return False


class Bridge:
    """Expose only the frontend API; never traverse monitor/window internals."""
    def __init__(self,api): self._api=api

    def snapshot(self): return self._api.snapshot()
    def action(self,name): return self._api.action(name)
    def set_device(self,params): return self._api.set_device(params)
    def save_settings(self,values): return self._api.save_settings(values)
    def choose_lhm(self): return self._api.choose_lhm()
    def preview_curve(self,points,temperature): return self._api.preview_curve(points,temperature)
    def ui_ready(self):
        (self._api.app_dir/'startup-status.json').write_text(json.dumps({'version':'1.0.01','ui_ready':True,'time':datetime.now().isoformat()}),encoding='utf-8')
        return True
    def get_version(self):
        """返回当前版本号"""
        return {'version':'1.0.01','repo':'https://github.com/GTE-vse/mi-fan'}
    def check_update(self):
        """请求多个 CDN 上的 version.json，比对版本号。带系统代理支持。"""
        import urllib.request
        import urllib.error
        LOCAL = '1.0.01'
        # 多源 fallback：国内镜像优先 → jsdelivr → 官方 raw
        REPO = 'GTE-vse/mi-fan'
        PATHS = [
            f'https://ghproxy.com/https://raw.githubusercontent.com/{REPO}/main/releases/version.json',
            f'https://cdn.jsdelivr.net/gh/{REPO}@main/releases/version.json',
            f'https://raw.githubusercontent.com/{REPO}/main/releases/version.json',
            f'https://mirror.ghproxy.com/https://raw.githubusercontent.com/{REPO}/main/releases/version.json',
        ]
        # 用系统代理（用户开 Clash/V2Ray 自动生效）
        proxy_handler = urllib.request.ProxyHandler(urllib.request.getproxies())
        opener = urllib.request.build_opener(proxy_handler)
        last_err = None
        for CDN in PATHS:
            try:
                req = urllib.request.Request(CDN, headers={'User-Agent':'MiFan/Updater'})
                with opener.open(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                remote = data.get('version','')
                newer = self._version_newer(remote, LOCAL)
                return {
                    'current': LOCAL,
                    'latest': remote,
                    'has_update': newer,
                    'notes': data.get('notes',''),
                    'download_url': data.get('download_url',''),
                    'mandatory': data.get('mandatory',False),
                    'ok': True,
                    'cdn': CDN.split('/')[2]  # 命中的 CDN 域名
                }
            except Exception as e:
                last_err = str(e)
                continue  # 试下一个源
        return {'ok': False, 'error': last_err or '所有更新源均不可达', 'current': LOCAL}
    @staticmethod
    def _version_newer(remote, local):
        """语义版本号比较：1.0.02 > 1.0.01"""
        def parse(v):
            parts = re.findall(r'\d+', v or '')
            return [int(p) for p in parts] or [0]
        a, b = parse(remote), parse(local)
        # 补齐长度
        n = max(len(a), len(b))
        a += [0]*(n-len(a)); b += [0]*(n-len(b))
        return a > b
    def set_caption(self, dark):
        """Windows DWM API —— 根据主题改标题栏颜色"""
        try:
            import ctypes
            DWMWA_CAPTION_COLOR=35
            DWMWA_TEXT_COLOR=36
            DWMWA_CAPTION_COLOR_POLICY=37
            # 0x00RRGGBB —— 深色 #141418 / 浅色 #FFF8F0
            caption=0x00181414 if dark else 0x00F0F8FF
            text=0x00E8E8EC if dark else 0x001a1a1a
            hwnd=None
            try:
                hwnd=self._api.window._hwnd if hasattr(self._api.window,'_hwnd') else None
            except: pass
            if not hwnd:
                hwnd=ctypes.windll.user32.FindWindowW(None,'Mi Fan · 米饭 — 小米空气净化器控制器')
            if hwnd:
                for attr,val in [(DWMWA_CAPTION_COLOR,caption),(DWMWA_TEXT_COLOR,text),(DWMWA_CAPTION_COLOR_POLICY,1)]:
                    try: ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd,attr,ctypes.byref(ctypes.c_int(val)),4)
                    except: pass
            return True
        except Exception: return True

    def toggle_autostart(self,enable): return self._api.toggle_autostart(bool(enable))
    def toggle_tray_minimize(self,enable): return self._api.toggle_tray_minimize(bool(enable))
    def get_tray_status(self): return self._api.get_tray_status()
    def show_window(self): self._api.show_window(); return True
    def hide_window(self): self._api.hide_window(); return True
    def handle_minimize_to_tray(self): return self._api.handle_minimize_to_tray()

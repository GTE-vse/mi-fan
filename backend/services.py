"""Background temperature sampling and serialized local device access."""
import copy
import os
import shutil
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from controller import temperature, send
from libre_process import ManagedLibre


def prepare_lhm(executable, url):
    path = Path(executable)
    if not path.is_file():
        raise RuntimeError('找不到 LibreHardwareMonitor.exe，请在设备页面选择正确路径。')
    config = path.with_suffix('.config')
    backup = config.with_suffix('.config.mifan-backup')
    if config.exists() and not backup.exists():
        shutil.copy2(config,backup)
    tree = ET.parse(config) if config.exists() else ET.ElementTree(ET.Element('configuration'))
    settings = tree.getroot().find('appSettings')
    if settings is None:
        settings = ET.SubElement(tree.getroot(), 'appSettings')
    # Confirmed against the installed MainForm constructor's settings identifiers.
    required = {'runWebServerMenuItem':'true', 'startMinMenuItem':'true',
                'minTrayMenuItem':'true', 'listenerIp':'127.0.0.1',
                'listenerPort':str(urlparse(url).port or 80), 'authenticationEnabled':'false'}
    for key, value in required.items():
        node = next((x for x in settings.findall('add') if x.get('key') == key), None)
        if node is None:
            node = ET.SubElement(settings,'add',key=key)
        node.set('value', value)
    temporary = config.with_suffix('.config.tmp')
    tree.write(temporary,encoding='utf-8',xml_declaration=True)
    temporary.replace(config)


def lhm_running():
    executable = Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/tasklist.exe'
    result = subprocess.run([str(executable),'/FI','IMAGENAME eq LibreHardwareMonitor.exe','/FO','CSV','/NH'],
                            capture_output=True,timeout=5,creationflags=0x08000000)
    return b'librehardwaremonitor.exe' in result.stdout.lower()


def launch_lhm(path, url):
    # Do not overwrite the configuration of an already running external monitor.
    if lhm_running():
        raise RuntimeError('Libre 已运行，但温度接口不可用。请退出 Libre 后点击“重连温度”，程序将自动配置并启动。')
    prepare_lhm(path,url)
    return ManagedLibre.launch(path)


class DeviceHub:
    """One lock for status reads and commands; no concurrent UDP sessions."""
    def __init__(self):
        self.lock = threading.RLock()
        self.device = None
        self.identity = None

    def connect(self, config):
        with self.lock:
            key = (config['ip'],config['token'],config['model'])
            if self.identity != key:
                from miio import Device
                device = Device(config['ip'],config['token'],timeout=3)
                if device.info().model != config['model']:
                    raise RuntimeError('净化器型号与设置不一致。')
                self.device, self.identity = device, key
            return self

    def info(self):
        with self.lock:
            return self.device.info()

    def bound(self,config):
        hub=self
        snapshot=copy.deepcopy(config)
        class BoundDevice:
            def info(self):
                with hub.lock:
                    hub.connect(snapshot)
                    return hub.info()
            def send(self,method,params):
                with hub.lock:
                    hub.connect(snapshot)
                    return hub.send(method,params)
        return BoundDevice()

    def send(self,method,params):
        with self.lock:
            return self.device.send(method,params,retry_count=1)

    def power(self,config,on):
        with self.lock:
            self.connect(config)
            if on:
                send(self,'set_mode',['auto'])
            send(self,'set_power',['on' if on else 'off'])

    def status(self,config):
        """Read a broad set of properties. Returns a dict with known keys; unknown keys omitted."""
        with self.lock:
            self.connect(config)
            prop_names=['power','mode','favorite_level','motor1_speed',
                'pm2_5','humidity','temp_dec','filter_life','filter_left_time',
                'illuminated','buzzer','led','child_lock','speed','fan_st_level','volume']
            state={}
            try:
                result=self.send('get_prop',prop_names)
                if isinstance(result,list) and len(result)==len(prop_names):
                    for k,v in zip(prop_names,result):
                        state[k]=v
                else:
                    # 某些型号不支持全部属性，逐个查询跳过失败的
                    for name in prop_names:
                        try:
                            r=self.send('get_prop',[name])
                            if isinstance(r,list) and r:
                                state[name]=r[0]
                        except Exception:
                            pass
            except Exception:
                # 批量查询整体失败，回退到逐个查询
                for name in prop_names:
                    try:
                        r=self.send('get_prop',[name])
                        if isinstance(r,list) and r:
                            state[name]=r[0]
                    except Exception:
                        pass
            if not state or state.get('power') not in ('on','off'):
                raise RuntimeError('设备返回的状态不可识别。')
            return state

    def set_mode(self,config,mode):
        """mode: 'auto' | 'favorite' | 'silent' | 'strong'"""
        with self.lock:
            self.connect(config)
            return send(self,'set_mode',[mode])

    def set_level(self,config,level):
        """Set favorite_level (0..max, model-dependent). Auto-switches to favorite mode."""
        with self.lock:
            self.connect(config)
            send(self,'set_level_favorite',[int(level)])
            return send(self,'set_mode',['favorite'])

    def led(self,config,on):
        with self.lock:
            self.connect(config)
            return send(self,'set_led',['on' if on else 'off'])

    def buzzer(self,config,on):
        with self.lock:
            self.connect(config)
            return send(self,'set_buzzer',['on' if on else 'off'])

    def child_lock(self,config,on):
        with self.lock:
            self.connect(config)
            return send(self,'set_child_lock',['on' if on else 'off'])

    def probe_level_max(self,config):
        """Auto-detect the maximum favorite_level by testing from 100 downward.
        Returns the highest supported level (int), or 16 on failure."""
        with self.lock:
            self.connect(config)
            # 先读当前档位，探测完恢复
            try:
                cur_r=self.send('get_prop',['favorite_level'])
                cur=cur_r[0] if isinstance(cur_r,list) and cur_r else None
            except Exception:
                cur=None
            # 先开电源（关机时某些设备不响应 set_level）
            was_off=False
            try:
                pwr=self.send('get_prop',['power'])
                if isinstance(pwr,list) and pwr and pwr[0]=='off':
                    was_off=True
                    try: send(self,'set_power',['on'])
                    except: pass
            except Exception:
                pass
            found=16
            for candidate in range(100,0,-1):
                try:
                    send(self,'set_level_favorite',[candidate])
                    time.sleep(0.08)
                    read=self.send('get_prop',['favorite_level'])
                    if isinstance(read,list) and read and int(read[0])==candidate:
                        found=candidate
                        break
                except Exception:
                    continue
            # 恢复现场
            if cur is not None:
                try: send(self,'set_level_favorite',[cur])
                except: pass
            if was_off:
                try: send(self,'set_power',['off'])
                except: pass
            return found

    def reset_filter(self,config):
        with self.lock:
            self.connect(config)
            return send(self,'reset_filter',[])


class LiveMonitor:
    def __init__(self, config, events, hub, reader=temperature, launcher=launch_lhm):
        self.config=copy.deepcopy(config)
        self.events=events
        self.hub=hub
        self.reader=reader
        self.launcher=launcher
        self.lock=threading.Lock()
        self.stop=threading.Event()
        self.wake=threading.Event()
        self.latest=None
        self.last_error='正在连接温度接口…'
        self.timestamp=0
        self.launch_attempted=False
        self.owned_monitors=[]
        self.sample_thread=None

    def update(self,config):
        with self.lock:
            source_changed=any(self.config.get(k)!=config.get(k) for k in ('lhm_url','sensor_name','lhm_path'))
            self.config=copy.deepcopy(config)
            if source_changed:
                self.latest=None
                self.launch_attempted=False
        self.wake.set()

    def cached(self,config):
        with self.lock:
            if self.latest is None or time.monotonic()-self.timestamp>max(10,config['poll_seconds']*3):
                raise RuntimeError(self.last_error or '温度数据已过期，等待监测恢复。')
            return self.latest

    def reconnect(self):
        with self.lock:
            self.launch_attempted=False
            self.latest=None
        self.wake.set()

    def sample_loop(self):
        last_error=None
        while not self.stop.is_set():
            with self.lock:
                config=copy.deepcopy(self.config)
            try:
                value=self.reader(config)
                with self.lock:
                    self.latest=value
                    self.timestamp=time.monotonic()
                    self.last_error=''
                self.events.put(('temperature',value))
                self.events.put(('sensor_status','实时监测中'))
                last_error=None
            except Exception as exc:
                with self.lock:
                    self.latest=None
                    self.last_error=str(exc)
                self.events.put(('temperature',None))
                self.events.put(('sensor_status','温度暂不可用'))
                if self.stop.is_set():
                    break
                if config.get('auto_start_lhm',True) and not self.launch_attempted:
                    self.launch_attempted=True
                    try:
                        self.events.put(('sensor_status','正在启动 Libre…'))
                        owned=self.launcher(config['lhm_path'],config['lhm_url'])
                        if owned is not None:
                            self.owned_monitors.append(owned)
                        self.events.put(('log','已启动 Libre，等待传感器就绪；首次运行可能需要允许 Windows 权限提示。'))
                    except Exception as launch_error:
                        self.events.put(('log',str(launch_error)))
                elif str(exc)!=last_error:
                    self.events.put(('log','温度接口暂不可用，请查看设备页面中的 Libre 路径与连接地址。'))
                last_error=str(exc)
            self.wake.wait(config['poll_seconds'])
            self.wake.clear()

    def device_loop(self):
        last_error=None
        while not self.stop.is_set():
            with self.lock:
                config=copy.deepcopy(self.config)
            if not config.get('ip') or not config.get('token'):
                self.events.put(('device_status',None))
                self.stop.wait(2)
                continue
            try:
                result=self.hub.status(config)
                self.events.put(('device_status',result))
                last_error=None
            except Exception as exc:
                self.events.put(('device_status',None))
                if str(exc)!=last_error:
                    self.events.put(('log','净化器状态读取失败：'+str(exc)))
                last_error=str(exc)
            self.stop.wait(10)

    def start(self):
        self.sample_thread=threading.Thread(target=self.sample_loop,daemon=True)
        self.sample_thread.start()
        threading.Thread(target=self.device_loop,daemon=True).start()

    def close(self):
        self.stop.set()
        self.wake.set()
        # Join before collecting handles, including a launch still awaiting UAC.
        if self.sample_thread is not None:
            self.sample_thread.join(timeout=15)
            if self.sample_thread.is_alive():
                raise RuntimeError('Libre 启动或采样尚未结束；如有权限提示请先处理，再关闭 AirLink。')
        for owned in list(self.owned_monitors):
            owned.close()
            self.owned_monitors.remove(owned)

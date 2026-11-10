import ipaddress, re, math, time
from urllib.parse import urlparse
from controller import temperature, select_band, send, level_max
from rule_engine import select_target, validate_curve, DEFAULT_CURVE

def validate(config, require_device=True):
    if require_device or config['ip']:
        try:
            ipaddress.IPv4Address(config['ip'])
        except ValueError:
            raise ValueError('请在设备连接中填写净化器的 IPv4 地址。')
    if (require_device or config['token']) and not re.fullmatch(r'[a-fA-F0-9]{32}', config['token']):
        raise ValueError('Token 必须是 32 位十六进制字符。')
    if config['model'] not in (
            'zhimi.airpurifier.m1', 'zhimi.airpurifier.ma2',
            'zhimi.airpurifier.m2', 'zhimi.airpurifier.ma1'):
        raise ValueError('此版本支持米家空气净化器 2/2S：zhimi.airpurifier.m1、zhimi.airpurifier.ma2 等。')
    if not config['sensor_name'] or not config['model']:
        raise ValueError('传感器名称和设备型号不能为空。')
    url = urlparse(config['lhm_url'])
    if url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('温度接口必须是本机 HTTP 地址，例如 http://127.0.0.1:8085/data.json。')
    numbers = config['thresholds_c'] + [config[k] for k in ('hysteresis_c', 'hold_seconds', 'poll_seconds')]
    if not all(math.isfinite(x) for x in numbers):
        raise ValueError('数值不能为无穷大或 NaN。')
    low, high = config['thresholds_c']
    if not 0 < low < high < 120:
        raise ValueError('温度阈值需要满足：0 < 中档阈值 < 高档阈值 < 120°C。')
    levels = config['favorite_levels']
    maxlvl = level_max(config['model'])
    if any(type(x) is not int or not 0 <= x <= maxlvl for x in levels) or levels != sorted(levels):
        raise ValueError(f'风量档位应为 0–{maxlvl} 的整数，且低温档 ≤ 中温档 ≤ 高温档。')
    if config.get('control_mode', 'bands') not in ('bands', 'curve'):
        raise ValueError('请选择三温区或自定义曲线模式。')
    validate_curve(config.get('curve_points', DEFAULT_CURVE), max_level=maxlvl)
    if config.get('control_mode', 'bands') == 'bands' and not 0 <= config['hysteresis_c'] < min(low, high-low):
        raise ValueError('回差需大于等于 0，且小于两个温度阈值的差值和中档阈值。')
    if config.get('control_mode') == 'curve' and not 0 <= config['hysteresis_c'] <= 20:
        raise ValueError('曲线降温回差范围为 0–20°C。')
    if not 0 <= config['hold_seconds'] <= 600 or not 1 <= config['poll_seconds'] <= 60:
        raise ValueError('稳定延时范围为 0–600 秒，采样间隔为 1–60 秒。')
    return config

def run_worker(config, apply, stop, events, reader=temperature, factory=None):
    device, touched = None, False
    current = pending = applied_level = None
    since = 0
    try:
        if apply:
            if factory is None:
                from miio import Device
                factory = Device
            events.put(('log', '正在连接净化器并核对型号…'))
            device = factory(config['ip'], config['token'])
            if device.info().model != config['model']:
                raise RuntimeError('设备型号与配置不一致，已停止。')
        events.put(('status', '联动运行中' if apply else '预览运行中'))
        while not stop.is_set():
            try:
                temp = reader(config)
            except Exception as exc:
                events.put(('reading', None, '—', '温度不可用'))
                events.put(('log', '温度读取失败：' + str(exc)))
                pending = None
                if touched:
                    send(device, 'set_mode', ['auto'])
                    touched = False
                    current = None
                    applied_level = None
                    events.put(('log', '温度中断，已恢复净化器自动模式。'))
                stop.wait(config['poll_seconds'])
                continue
            if stop.is_set():
                break
            target, level = select_target(temp, current, config)
            now = time.monotonic()
            if pending != target:
                pending, since = target, now
            remaining = max(0, config['hold_seconds'] - (now-since))
            state = f'等待稳定 {math.ceil(remaining)} 秒' if target != current else ('已应用' if apply else '已模拟')
            events.put(('reading', temp, level, state))
            if target != current and remaining == 0:
                if device and level != applied_level:
                    touched = True
                    send(device, 'set_power', ['on'])
                    if stop.is_set():
                        break
                    send(device, 'set_level_favorite', [level])
                    if stop.is_set():
                        break
                    send(device, 'set_mode', ['favorite'])
                current = target
                if level != applied_level:
                    events.put(('log', ('已应用' if apply else '预览模拟') + f'风量档位 {level}'))
                applied_level = level
            stop.wait(config['poll_seconds'])
    except Exception as exc:
        events.put(('log', '运行已停止：' + str(exc)))
    finally:
        if touched and config['restore_auto_on_exit']:
            events.put(('status', '正在恢复自动模式…'))
            try:
                send(device, 'set_mode', ['auto'])
                events.put(('log', '已恢复净化器自动模式。'))
            except Exception:
                events.put(('log', '恢复自动模式失败，请在米家 App 中检查设备。'))
        events.put(('done',))

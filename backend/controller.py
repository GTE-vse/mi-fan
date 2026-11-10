"""CPU temperature -> Mi Air Purifier 2 favorite level. Preview by default."""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def find_powershell():
    windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    candidates = [
        windows / 'Sysnative/WindowsPowerShell/v1.0/powershell.exe',
        windows / 'System32/WindowsPowerShell/v1.0/powershell.exe',
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    executable = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if executable:
        return executable
    raise RuntimeError('PowerShell not found. Enable LHM Remote Web Server on port 8085.')


def select_temperature(sensors, sensor_name):
    """Prefer a CPU package/control sensor; never auto-select GPU or motherboard."""
    usable = []
    for sensor in sensors:
        try:
            value = float(sensor['value'])
        except (ValueError, TypeError):
            continue
        if math.isfinite(value) and 0 < value < 120:
            usable.append(dict(sensor, value=value))
    if sensor_name.casefold() not in ('auto', '自动'):
        matches = [s['value'] for s in usable if s['name'] == sensor_name]
        if len(matches) != 1:
            raise RuntimeError('温度传感器缺失或重名，请核对传感器名称，或填写 auto。')
        return matches[0]
    cpu = [s for s in usable if any(part in s['identifier'].lower()
                                  for part in ('/amdcpu/', '/intelcpu/'))]
    for preferred in ('Core (Tctl/Tdie)', 'CPU Package', 'Core (Tdie)',
                      'Core (Tctl)', 'Core Average', 'Core Max'):
        matches = [s['value'] for s in cpu if s['name'] == preferred]
        if matches:
            return max(matches)
    cores = [s['value'] for s in cpu if re.match(r'^(CPU )?Core #?\d+', s['name'])]
    if cores:
        return max(cores)
    raise RuntimeError('未识别到 CPU 温度。请在 Libre 中确认 CPU 温度可见，或在设备连接中填写准确的传感器名称。')


def web_temperatures(tree):
    sensors = []
    def visit(node):
        if isinstance(node, dict):
            identifier = str(node.get('SensorId', node.get('Identifier', '')))
            value = str(node.get('Value', ''))
            if '°C' in value or ('temperature' in identifier.lower() and 'C' in value):
                match = re.search(r'-?\d+(?:[.,]\d+)?', value)
                if match:
                    sensors.append(dict(name=str(node.get('Text', '')), identifier=identifier,
                                        value=float(match.group().replace(',', '.'))))
            for child in node.get('Children', []):
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(tree)
    return sensors


def temperature(config):
    # Read only loopback LHM web data; fall back to its WMI provider.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(config['lhm_url'], timeout=3) as response:
            tree = json.load(response)
        result = select_temperature(web_temperatures(tree), config['sensor_name'])
    except Exception as web_error:
        command = (
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; "
            "@(Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor | "
            "Where-Object SensorType -eq 'Temperature' | "
            "Select-Object Name,Value,Identifier) | ConvertTo-Json -Compress"
        )
        process = subprocess.run(
            [find_powershell(), '-NoProfile', '-Command', command],
            capture_output=True, timeout=8, creationflags=0x08000000,
        )
        if process.returncode:
            raise RuntimeError(
                'LHM web data unavailable (' + str(web_error) + '). '
                'WMI also unavailable. In Libre Hardware Monitor, enable '
                'Options > Remote Web Server > Run, port 8085; '
                'check http://127.0.0.1:8085/data.json in your browser.'
            )
        sensors = json.loads(process.stdout.decode('utf-8-sig'))
        if isinstance(sensors, dict):
            sensors = [sensors]
        result = select_temperature([dict(name=s['Name'], value=s['Value'],
                                          identifier=s.get('Identifier', ''))
                                     for s in (sensors or [])], config['sensor_name'])
    if not math.isfinite(result) or not 0 < result < 120:
        raise RuntimeError('Invalid CPU temperature')
    return result


def select_band(temp, current, thresholds, hysteresis):
    if current is None:
        return sum(temp >= boundary for boundary in thresholds)
    while current < len(thresholds) and temp >= thresholds[current]:
        current += 1
    while current > 0 and temp < thresholds[current - 1] - hysteresis:
        current -= 1
    return current


def resolve_level_max(model, config=None):
    """Return level_max, preferring cached probe result in config['level_max']."""
    if config and config.get('level_max'):
        return int(config['level_max'])
    return level_max(model)


def level_max(model):
    """Return the maximum favorite_level accepted by the device model (known mapping)."""
    # 14档 (Air Purifier 2S / ma2)
    if model in ('zhimi.airpurifier.ma2', 'zhimi.airpurifier.ma4'):
        return 14
    # 16档 (经典米家 1/2/2s/ma1/mb1)
    if model in ('zhimi.airpurifier.m1', 'zhimi.airpurifier.m2',
                 'zhimi.airpurifier.ma1', 'zhimi.airpurifier.mb1',
                 'zhimi.airpurifier.mb2', 'zhimi.airpurifier.ma3',
                 'zhimi.airpurifier.ma5', 'zhimi.airpurifier.ma6',
                 'zhimi.airpurifier.mc1', 'zhimi.airpurifier.mc2'):
        return 16
    # 100档 (Pro 系列 / 数显百分比)
    if model in ('zhimi.airpurifier.v5', 'zhimi.airpurifier.v6',
                 'zhimi.airpurifier.v7', 'zhimi.airpurifier.v8',
                 'zhimi.airpurifier.pro', 'zhimi.airpurifier.proh',
                 'zhimi.airpurifier.pro2', 'zhimi.airpurifier.pro2h',
                 'zhimi.airpurifier.xa1'):
        return 100
    # 默认 16（unknown 型号，设备连接后会自动探测覆盖）
    return 16


def send(device, method, params):
    result = device.send(method, params)
    if result != ['ok']:
        raise RuntimeError('Device did not acknowledge ' + method)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Enable physical control')
    parser.add_argument('--once', action='store_true', help='Read one temperature, preview only')
    args = parser.parse_args()
    if args.apply and args.once:
        parser.error('--once is preview only')
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8-sig'))
    thresholds = config['thresholds_c']
    levels = config['favorite_levels']
    if len(thresholds) != 2 or thresholds != sorted(set(thresholds)):
        raise ValueError('Use two increasing temperature thresholds')
    maxlvl = level_max(config['model'])
    if len(levels) != 3 or any(type(v) is not int or not 0 <= v <= maxlvl for v in levels):
        raise ValueError(f'Use three integer favorite levels in 0..{maxlvl}')
    if config['poll_seconds'] <= 0 or config['hold_seconds'] < 0 or config['hysteresis_c'] < 0:
        raise ValueError('Invalid timing or hysteresis')
    device = None
    touched = False
    if args.apply:
        from miio import Device
        device = Device(config['ip'], config['token'])
        info = device.info()
        if info.model != config['model']:
            raise RuntimeError('Device model does not match config')
    current = pending = None
    since = 0
    print('CONTROL enabled' if args.apply else 'PREVIEW only: no device commands', flush=True)
    try:
        while True:
            try:
                temp = temperature(config)
            except Exception as exc:
                print('Temperature unavailable: ' + str(exc), flush=True)
                pending = None
                if args.once:
                    return 1
                if touched:
                    # Relinquish control on loss of temperature; fail visibly if unreachable.
                    send(device, 'set_mode', ['auto'])
                    touched = False
                    current = None
                time.sleep(config['poll_seconds'])
                continue
            target = select_band(temp, current, thresholds, config['hysteresis_c'])
            now = time.monotonic()
            if target != pending:
                pending, since = target, now
            print(f'CPU {temp:.1f} C | target level {levels[target]} | active band {current}', flush=True)
            if args.once:
                return 0
            if target != current and now - since >= config['hold_seconds']:
                if device:
                    touched = True
                    send(device, 'set_power', ['on'])
                    send(device, 'set_level_favorite', [levels[target]])
                    send(device, 'set_mode', ['favorite'])
                current = target
                print(f'Level {levels[current]} ' + ('applied' if device else 'simulated'), flush=True)
            time.sleep(config['poll_seconds'])
    except KeyboardInterrupt:
        print('Stopping...', flush=True)
    finally:
        if touched and config['restore_auto_on_exit']:
            try:
                send(device, 'set_mode', ['auto'])
                print('Purifier returned to auto mode', flush=True)
            except Exception:
                print('Could not restore auto mode; check Mi Home app', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

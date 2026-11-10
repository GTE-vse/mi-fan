"""Shared curve calculation for desktop previews and actual device control."""
import math

from controller import select_band

DEFAULT_CURVE = [
    {'temp_c': 40, 'level': 0}, {'temp_c': 55, 'level': 0},
    {'temp_c': 65, 'level': 1}, {'temp_c': 75, 'level': 2},
    {'temp_c': 85, 'level': 3},
]
MEASURED_RPM = {0: 348, 1: 700, 2: 746, 3: 799}


def validate_curve(points, max_level=16):
    if not isinstance(points, list) or not 2 <= len(points) <= 12:
        raise ValueError('曲线需要 2–12 个控制点。')
    previous_temp, previous_level = -1, -1
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('控制点格式无效。')
        temp, level = point.get('temp_c'), point.get('level')
        if type(temp) not in (int, float) or not math.isfinite(temp) or not 0 < temp < 120:
            raise ValueError('控制点温度必须在 0–120°C 之间。')
        if temp <= previous_temp:
            raise ValueError('控制点温度必须递增，不能重复。')
        if type(level) is not int or not 0 <= level <= max_level:
            raise ValueError(f'控制点档位必须是 0–{max_level} 的整数。')
        if level < previous_level:
            raise ValueError('温度升高时，曲线档位不能降低。')
        previous_temp, previous_level = temp, level
    return points


def interpolate(temp, points):
    if temp <= points[0]['temp_c']:
        return float(points[0]['level'])
    for a, b in zip(points, points[1:]):
        if temp <= b['temp_c']:
            ratio = (temp - a['temp_c']) / (b['temp_c'] - a['temp_c'])
            return a['level'] + (b['level'] - a['level']) * ratio
    return float(points[-1]['level'])


def quantize(value, falling=False):
    # Quietest at an exact tie; while falling, keep the active upper level at the boundary.
    return math.floor(value + .5 + 1e-10) if falling else math.ceil(value - .5 - 1e-10)


def select_target(temp, current, config):
    """Return rule state and target level. State is band index or active curve level."""
    if config.get('control_mode', 'bands') == 'bands':
        band = select_band(temp, current, config['thresholds_c'], config['hysteresis_c'])
        return band, config['favorite_levels'][band]
    points = config['curve_points']
    target = quantize(interpolate(temp, points))
    if current is not None and target < current:
        held = quantize(interpolate(temp + config['hysteresis_c'], points), falling=True)
        target = min(current, max(target, held))
    return target, target


def curve_preview(points, temp, max_level=16):
    validate_curve(points, max_level)
    if type(temp) not in (int, float) or not math.isfinite(temp) or not 0 <= temp <= 120:
        raise ValueError('预览温度必须在 0–120°C 之间。')
    steps = [dict(points[0])]
    boundaries = []
    for a, b in zip(points, points[1:]):
        for level in range(a['level'], b['level']):
            crossing = a['temp_c'] + (level + .5 - a['level']) / (b['level'] - a['level']) * (b['temp_c'] - a['temp_c'])
            steps.extend([{'temp_c': crossing, 'level': level}, {'temp_c': crossing, 'level': level + 1}])
            boundaries.append({'temp_c': crossing, 'from_level': level, 'to_level': level + 1})
    steps.append(dict(points[-1]))
    ideal = interpolate(temp, points)
    level = quantize(ideal)
    return dict(steps=steps, boundaries=boundaries, ideal_level=ideal, target_level=level,
                reference_rpm=MEASURED_RPM.get(level))

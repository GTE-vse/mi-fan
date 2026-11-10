import json
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from api import Api
from controller import select_temperature, web_temperatures
from portable_runtime import prerequisites, ensure_runtime
from services import LiveMonitor


def sensor(name, value, identifier='/intelcpu/0/temperature/0'):
    return dict(name=name, value=value, identifier=identifier)


class PortableTests(unittest.TestCase):
    def test_amd_control_sensor_wins_over_ccd_and_gpu(self):
        samples = [sensor('GPU Core', 90, '/gpu-nvidia/0/temperature/0'),
                   sensor('Core (Tctl/Tdie)', 52.5, '/amdcpu/0/temperature/0'),
                   sensor('CCD1 (Tdie)', 60, '/amdcpu/0/temperature/1')]
        self.assertEqual(select_temperature(samples, 'auto'), 52.5)

    def test_intel_package_and_multiple_cpus(self):
        self.assertEqual(select_temperature([sensor('CPU Package', 48),
            sensor('CPU Package', 61, '/intelcpu/1/temperature/0'), sensor('CPU Core #1', 75)], 'auto'), 61)

    def test_cpu_core_fallback_ignores_gpu_and_board(self):
        self.assertEqual(select_temperature([sensor('CPU Core #1', 53), sensor('CPU Core #2', 56),
            sensor('CPU Package', 99, '/lpc/temperature/0')], 'auto'), 56)

    def test_auto_fails_without_identified_cpu(self):
        with self.assertRaises(RuntimeError):
            select_temperature([sensor('CPU Package', 50, '/lpc/temperature/0')], 'auto')

    def test_explicit_sensor_keeps_exact_matching(self):
        self.assertEqual(select_temperature([sensor('Custom CPU', 47, '/custom/0')], 'Custom CPU'), 47)
        with self.assertRaises(RuntimeError):
            select_temperature([sensor('Custom CPU', 47), sensor('Custom CPU', 49)], 'Custom CPU')

    def test_invalid_temperatures_not_used(self):
        for value in (None, float('nan'), float('inf'), -10, 0, 120):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                select_temperature([sensor('CPU Package', value)], 'auto')

    def test_web_tree_uses_sensor_identity_and_decimal_comma(self):
        tree = {'Children': [{'Text': 'CPU Package', 'SensorId': '/intelcpu/0/temperature/0',
                              'Value': '48,5 °C'},
                             {'Text': 'CPU Package', 'Value': '99 %'},
                             {'Text': 'GPU Core', 'SensorId': '/gpu-nvidia/0/temperature/0', 'Value': '77 °C'}]}
        self.assertEqual(select_temperature(web_temperatures(tree), 'auto'), 48.5)

    def test_first_run_settings_save_without_device_and_no_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            hub=Mock()
            api=Api(folder, monitoring=False, hub=hub)
            self.assertEqual(api.config['ip'], '')
            self.assertEqual(api.config['token'], '')
            self.assertEqual(api.config['sensor_name'], 'auto')
            self.assertTrue(api.save_settings({'theme':'dark'})['ok'])
            self.assertFalse(api.action('start')['ok'])
            self.assertFalse(api.action('on')['ok'])
            hub.assert_not_called()
            self.assertFalse(hub.method_calls)
            api.shutdown()

    def test_bundled_path_stays_portable_when_moved(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            for folder in (first, second):
                exe=Path(folder)/'LibreHardwareMonitor/LibreHardwareMonitor.exe'
                exe.parent.mkdir()
                exe.write_bytes(b'fixture')
            api=Api(first,monitoring=False)
            self.assertTrue(api.save_settings({'theme':'dark'})['ok'])
            saved=json.loads(api.path.read_text(encoding='utf-8'))
            self.assertEqual(saved['lhm_path'],'LibreHardwareMonitor/LibreHardwareMonitor.exe')
            (Path(second)/'config.json').write_text(json.dumps(saved),encoding='utf-8')
            moved=Api(second,monitoring=False)
            self.assertEqual(Path(moved.config['lhm_path']),Path(second)/saved['lhm_path'])
            api.shutdown();moved.shutdown()

    def test_unconfigured_device_does_not_poll_lan(self):
        monitor=LiveMonitor(dict(ip='',token=''),queue.Queue(),Mock())
        monitor.stop=Mock()
        monitor.stop.is_set.side_effect=[False,True]
        monitor.device_loop()
        monitor.hub.status.assert_not_called()

    def test_extractor_only_launches_from_explicit_action(self):
        with tempfile.TemporaryDirectory() as folder, patch('api.subprocess.Popen') as spawn:
            api=Api(folder,monitoring=False)
            spawn.assert_not_called()
            self.assertFalse(api.action('extract_token')['ok'])
            spawn.assert_not_called()
            tool=Path(folder)/'工具/token_extractor.exe'
            tool.parent.mkdir();tool.write_bytes(b'fixture')
            self.assertTrue(api.action('extract_token')['ok'])
            self.assertEqual(spawn.call_args.args[0],[str(tool)])
            api.shutdown()

    def test_runtime_detection_rejects_missing_and_zero_versions(self):
        with patch('portable_runtime.registry_value',return_value=None):
            self.assertEqual(prerequisites(),{'net':False,'webview':False})
        def query(hive,path,name,view):
            return 528040 if name=='Release' else '153.0.1.0'
        with patch('portable_runtime.registry_value',side_effect=query):
            self.assertEqual(prerequisites(),{'net':True,'webview':True})

    def test_runtime_installer_never_runs_without_consent(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('portable_runtime.prerequisites',return_value={'net':True,'webview':False}), \
             patch('portable_runtime.message',return_value=7), \
             patch('portable_runtime.run_installer') as run:
            self.assertFalse(ensure_runtime(Path(folder)))
            run.assert_not_called()


if __name__=='__main__': unittest.main()

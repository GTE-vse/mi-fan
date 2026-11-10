import ctypes
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from api import Api
from services import LiveMonitor, launch_lhm
from libre_process import kernel, creation_time, supervise, stop_child


class MonitorLifetimeTests(unittest.TestCase):
    def make_monitor(self, reader, launcher):
        return LiveMonitor(dict(poll_seconds=60, auto_start_lhm=True,
                                lhm_path='Libre.exe', lhm_url='http://127.0.0.1:8085'),
                           queue.Queue(), Mock(), reader=reader, launcher=launcher)

    def start_sample(self, monitor):
        monitor.sample_thread = threading.Thread(target=monitor.sample_loop, daemon=True)
        monitor.sample_thread.start()

    def test_reused_external_monitor_is_not_closed(self):
        sampled = threading.Event()
        def reader(config):
            sampled.set()
            return 50
        launcher = Mock()
        monitor = self.make_monitor(reader, launcher)
        self.start_sample(monitor)
        self.assertTrue(sampled.wait(2))
        monitor.close()
        monitor.close()
        launcher.assert_not_called()

    def test_existing_unavailable_libre_is_not_adopted(self):
        with patch('services.lhm_running', return_value=True), \
             patch('services.ManagedLibre.launch') as launch:
            with self.assertRaises(RuntimeError):
                launch_lhm('Libre.exe', 'http://127.0.0.1:8085')
            launch.assert_not_called()

    def test_close_during_launch_waits_and_cleans_once(self):
        launching, allow_launch, closed = threading.Event(), threading.Event(), threading.Event()
        owned = Mock()
        def launch(*args):
            launching.set()
            if not allow_launch.wait(3):
                raise RuntimeError('test launch timed out')
            return owned
        monitor = self.make_monitor(Mock(side_effect=RuntimeError('offline')), launch)
        self.start_sample(monitor)
        self.assertTrue(launching.wait(2))
        errors = []
        def close():
            try:
                monitor.close()
            except Exception as exc:
                errors.append(exc)
            finally:
                closed.set()
        thread = threading.Thread(target=close)
        thread.start()
        self.assertTrue(monitor.stop.wait(2))
        self.assertFalse(closed.is_set())
        allow_launch.set()
        thread.join(3)
        self.assertTrue(closed.is_set())
        self.assertEqual(errors, [])
        owned.close.assert_called_once()
        monitor.close()
        owned.close.assert_called_once()

    def test_stop_during_failed_read_does_not_launch(self):
        reading, release = threading.Event(), threading.Event()
        def read(config):
            reading.set()
            release.wait(2)
            raise RuntimeError('offline')
        launcher = Mock()
        monitor = self.make_monitor(read, launcher)
        self.start_sample(monitor)
        self.assertTrue(reading.wait(2))
        monitor.stop.set()
        release.set()
        monitor.close()
        launcher.assert_not_called()

    def test_close_failure_keeps_handle_for_retry(self):
        monitor = self.make_monitor(Mock(), Mock())
        owned = Mock()
        owned.close.side_effect = [RuntimeError('still closing'), None]
        monitor.owned_monitors.append(owned)
        with self.assertRaises(RuntimeError):
            monitor.close()
        self.assertEqual(monitor.owned_monitors, [owned])
        monitor.close()
        self.assertEqual(monitor.owned_monitors, [])

    def test_shutdown_restores_link_before_stopping_libre(self):
        with tempfile.TemporaryDirectory() as folder:
            api = Api(folder, monitoring=False, hub=Mock())
            calls = []
            api._stop_link = lambda: calls.append('link')
            api.monitor.close = lambda: calls.append('libre')
            api.shutdown()
            self.assertEqual(calls, ['link', 'libre'])
            self.assertTrue(api.shutdown_event.is_set())

    def test_graceful_exit_does_not_terminate(self):
        child = Mock()
        child.poll.return_value = None
        with patch('libre_process.request_window_close') as close:
            stop_child(child)
            close.assert_called_once_with(child.pid)
        child.wait.assert_called_once_with(timeout=6)
        child.terminate.assert_not_called()

    def test_unresponsive_child_uses_exact_owned_handle(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('stub', 6), 1]
        with patch('libre_process.request_window_close'):
            stop_child(child)
        child.terminate.assert_called_once()

    def test_already_exited_child_does_not_receive_close(self):
        child = Mock()
        child.poll.return_value = 0
        with patch('libre_process.request_window_close') as close:
            stop_child(child)
            close.assert_not_called()
        child.terminate.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Windows process lifecycle integration')
class WindowsSupervisorTests(unittest.TestCase):
    def exercise(self, parent_exit=False, already_stopped=False):
        api = kernel()
        name = 'Local\\AirLinkLibre-Test-' + uuid.uuid4().hex
        event = api.CreateEventW(None, True, already_stopped, name)
        self.assertTrue(event)
        parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        handle = api.OpenProcess(0x1000, False, parent.pid)
        created = creation_time(api, handle)
        api.CloseHandle(handle)
        children, failures = [], []
        with tempfile.TemporaryDirectory() as folder:
            ready = Path(folder) / 'ready'
            clean = Path(folder) / 'clean'
            fixture = Path(__file__).parent / 'fixtures/libre_window.py'
            def spawn(*args, **kwargs):
                child = subprocess.Popen([sys.executable, str(fixture), str(ready), str(clean)],
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                children.append(child)
                return child
            def run():
                try:
                    supervise(parent.pid, created, name, sys.executable, spawn=spawn)
                except Exception as exc:
                    failures.append(exc)
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                if not already_stopped:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(ready.exists(), 'stub window was not created')
                    if parent_exit:
                        parent.terminate()
                        parent.wait(timeout=3)
                    else:
                        api.SetEvent(event)
                worker.join(12)
                self.assertFalse(worker.is_alive())
                self.assertEqual(failures, [])
                if already_stopped:
                    self.assertEqual(children, [])
                else:
                    # Restricted desktops may hide the child's windows from EnumWindows;
                    # in that case this exercises the actual owned-handle fallback.
                    self.assertIn(children[0].poll(), (0, 1))
                    if clean.exists():
                        self.assertEqual(clean.read_text(), 'graceful')
                        self.assertEqual(children[0].poll(), 0)
                    else:
                        self.assertEqual(children[0].poll(), 1)
                if not parent_exit:
                    self.assertIsNone(parent.poll(), 'unrelated parent must stay running')
            finally:
                api.SetEvent(event)
                worker.join(12)
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=3)
                if parent.poll() is None:
                    parent.kill()
                parent.wait(timeout=3)
                api.CloseHandle(event)

    def test_close_event_exits_owned_process(self):
        self.exercise()

    def test_parent_exit_exits_owned_process(self):
        self.exercise(parent_exit=True)

    def test_cancelled_start_never_spawns_child(self):
        self.exercise(already_stopped=True)


if __name__ == '__main__':
    unittest.main()

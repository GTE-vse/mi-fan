import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from api import Api


class Hub:
    def __init__(self): self.calls=[]
    def power(self,c,on): self.calls.append(('power',on))
    def bound(self,c): return self
    def info(self):
        class Info: model='zhimi.airpurifier.m1'
        return Info()
    def send(self,m,p): self.calls.append((m,p));return ['ok']


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.hub=Hub()
        self.api=Api(self.tmp.name,monitoring=False,hub=self.hub)
        self.api.config['token']='a'*32
        self.api.config['ip']='192.0.2.1'
        self.api.config['hold_seconds']=0
        self.api.monitor.latest=46
        self.api.monitor.timestamp=time.monotonic()

    def tearDown(self): self.api.shutdown();self.tmp.cleanup()

    def test_snapshot_hides_token(self):
        self.assertNotIn('token',self.api.snapshot()['config'])
        self.api.log('exception with '+'a'*32)
        self.assertNotIn('a'*32,json.dumps(self.api.snapshot()))

    def test_save_keeps_existing_token_and_validates(self):
        self.assertTrue(self.api.save_settings({'token':'','thresholds_c':[50,75]})['ok'])
        self.assertEqual(json.loads(self.api.path.read_text())['token'],'a'*32)
        self.assertFalse(self.api.save_settings({'thresholds_c':[80,50]})['ok'])
        self.assertEqual(self.api.config['thresholds_c'],[50,75])

    def test_power_off_waits_for_restore(self):
        self.assertTrue(self.api.action('start')['ok'])
        for _ in range(100):
            if ('set_mode',['favorite']) in self.hub.calls: break
            time.sleep(.005)
        self.assertTrue(self.api.action('off')['ok'])
        self.assertEqual(self.hub.calls[-2:],[('set_mode',['auto']),('power',False)])
        self.assertFalse(self.api.snapshot()['state']['linked'])

    def test_idle_updates_temperature(self):
        self.api.consume_event(('temperature',52.5))
        self.assertEqual(self.api.snapshot()['state']['temp'],52.5)
        self.assertFalse(self.api.snapshot()['state']['linked'])

    def test_stale_link_events_ignored(self):
        self.api.generation=2
        self.api.state['linked']=True
        self.api.consume_event(('link',1,('done',)))
        self.assertTrue(self.api.state['linked'])

    def test_start_without_temperature_cannot_control(self):
        self.api.monitor.latest=None
        self.assertFalse(self.api.action('start')['ok'])
        self.assertFalse(self.hub.calls)

    def test_saves_blocked_while_linked(self):
        self.api.state['linked']=True
        self.assertFalse(self.api.save_settings({'ip':'192.168.1.9'})['ok'])


if __name__=='__main__': unittest.main()

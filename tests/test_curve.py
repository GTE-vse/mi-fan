import copy
import json
from pathlib import Path
import queue
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from api import Api
from rule_engine import DEFAULT_CURVE, curve_preview, interpolate, select_target, validate_curve
from link_logic import run_worker
from unittest.mock import patch


class CurveTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(control_mode='curve',curve_points=copy.deepcopy(DEFAULT_CURVE),
                           thresholds_c=[65,75],favorite_levels=[2,3,5],hysteresis_c=3,
                           hold_seconds=6,poll_seconds=3,ip='192.0.2.1',token='a'*32,
                           model='zhimi.airpurifier.m1',restore_auto_on_exit=True)

    def test_interpolation_endpoint_clamping_and_quiet_tie(self):
        for temp, ideal, level in [(-5,0,0),(55,0,0),(60,.5,0),(61,.6,1),(65,1,1),(70,1.5,1),(71,1.6,2),(200,3,3)]:
            self.assertAlmostEqual(interpolate(temp,DEFAULT_CURVE),ideal)
            self.assertEqual(select_target(temp,None,self.config),(level,level))

    def test_falling_hysteresis_is_in_degrees_not_levels(self):
        self.assertEqual(select_target(58,1,self.config)[1],1)
        self.assertEqual(select_target(57,1,self.config)[1],1)
        self.assertEqual(select_target(56.9,1,self.config)[1],0)
        self.assertEqual(select_target(80.1,0,self.config)[1],3)
        self.assertEqual(select_target(45,3,self.config)[1],0)

    def test_preview_boundaries_match_device_selection(self):
        result=curve_preview(DEFAULT_CURVE,61)
        self.assertEqual([b['temp_c'] for b in result['boundaries']],[60,70,80])
        for boundary in result['boundaries']:
            self.assertEqual(select_target(boundary['temp_c'],None,self.config)[1],boundary['from_level'])
            self.assertEqual(select_target(boundary['temp_c']+.001,None,self.config)[1],boundary['to_level'])
        self.assertEqual(result['reference_rpm'],700)
        self.assertIsNone(curve_preview([{'temp_c':40,'level':4},{'temp_c':80,'level':8}],40)['reference_rpm'])

    def test_points_reject_duplicates_nonmonotonic_and_nonfinite(self):
        bad=[[],[{'temp_c':40,'level':0}],
             [{'temp_c':40,'level':0},{'temp_c':40,'level':1}],
             [{'temp_c':40,'level':2},{'temp_c':50,'level':1}],
             [{'temp_c':40,'level':0},{'temp_c':float('nan'),'level':1}],
             [{'temp_c':40,'level':0},{'temp_c':50,'level':1.5}],
             [{'temp_c':40,'level':0},{'temp_c':50,'level':True}]]
        for points in bad:
            with self.subTest(points=points),self.assertRaises(ValueError):validate_curve(points)

    def test_flat_curve_and_nonzero_floor(self):
        self.config['curve_points']=[{'temp_c':40,'level':2},{'temp_c':60,'level':2},{'temp_c':85,'level':2}]
        for temp in (0,40,50,60,100):self.assertEqual(select_target(temp,None,self.config)[1],2)
        self.assertEqual(curve_preview(self.config['curve_points'],50)['boundaries'],[])

    def run_sequence(self,readings):
        class Stop:
            index=0
            now=0
            def is_set(self):return self.index>=len(readings)
            def wait(self,seconds):self.now+=seconds;self.index+=1
        class Device:
            def __init__(self):self.calls=[]
            def info(self):
                class Info:model='zhimi.airpurifier.m1'
                return Info()
            def send(self,method,params):self.calls.append((method,params));return ['ok']
        stop=Stop();device=Device();events=queue.Queue()
        def reader(config):
            value=readings[stop.index]
            if value is None:raise RuntimeError('temperature lost')
            return value
        with patch('link_logic.time.monotonic',side_effect=lambda:stop.now):
            run_worker(self.config,True,stop,events,reader=reader,factory=lambda *args:device)
        return device.calls,list(events.queue)

    def test_pending_target_must_stabilize_and_same_level_is_not_resent(self):
        calls,events=self.run_sequence([59,61,59,61,61,61,61,61])
        self.assertEqual([p[0] for m,p in calls if m=='set_level_favorite'],[1])
        self.assertEqual(calls[-1],('set_mode',['auto']))
        self.assertEqual(sum('已应用风量档位' in str(e) for e in events),1)

    def test_temperature_loss_restores_and_recovery_reapplies(self):
        calls,_=self.run_sequence([65,65,65,None,65,65,65])
        self.assertEqual([p[0] for m,p in calls if m=='set_level_favorite'],[1,1])
        self.assertEqual(sum(m=='set_mode' and p==['auto'] for m,p in calls),2)

    def test_existing_bands_still_work(self):
        self.config['control_mode']='bands'
        self.assertEqual(select_target(64,None,self.config),(0,2))
        self.assertEqual(select_target(65,None,self.config),(1,3))
        self.assertEqual(select_target(73,2,self.config),(2,5))
        self.assertEqual(select_target(71.9,2,self.config),(1,3))

    def test_old_config_migration_curve_persistence_and_gap_recording(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'config.json'
            old=dict(self.config);old.pop('control_mode');old.pop('curve_points')
            path.write_text(json.dumps(old))
            api=Api(temporary,monitoring=False)
            try:
                self.assertEqual(api.config['control_mode'],'bands')
                self.assertEqual(api.config['favorite_levels'],[2,3,5])
                api.consume_event(('temperature',61))
                self.assertTrue(api.save_settings({'control_mode':'curve'})['ok'])
                self.assertEqual(api.state['target'],1)
                saved=json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(saved['token'],old['token'])
                self.assertEqual(saved['control_mode'],'curve')
                self.assertFalse(api.save_settings({'curve_points':[{'temp_c':40,'level':0},{'temp_c':40,'level':1}]})['ok'])
                api.consume_event(('temperature',None))
                self.assertIsNone(api.history[-1]['v'])
                self.assertIsNone(api.state['target'])
                self.assertTrue(api.preview_curve(DEFAULT_CURVE,61)['ok'])
            finally:api.shutdown()

    def test_history_window_uses_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            api=Api(temporary,monitoring=False)
            try:
                with patch('api.time.time',return_value=1000):api.consume_event(('temperature',45))
                with patch('api.time.time',return_value=1601):api.consume_event(('temperature',46))
                self.assertEqual(list(api.history),[{'t':1601,'v':46}])
            finally:api.shutdown()


if __name__=='__main__':unittest.main()

import tempfile
import unittest
from pathlib import Path
from hyperv_acu import input_guard as guard

class InputGuardTest(unittest.TestCase):
    def test_pending_and_uncertain_block_further_input(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input.json'
            guard.require_clear(path)
            guard.start(path,'target')
            with self.assertRaises(ValueError):guard.require_clear(path)
            with self.assertRaises(ValueError):guard.start(path,'another')
            guard.finish(path,{'outcome':'input_outcome_unknown'})
            with self.assertRaises(ValueError):guard.require_clear(path)
            guard.observed(path,'target')
            with self.assertRaises(ValueError):guard.require_clear(path)
            with self.assertRaises(ValueError):guard.confirm(path,'target','anything','Inspected the resulting screen')

    def test_delivered_needs_observation_of_same_target(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input.json'
            guard.start(path,'target');guard.finish(path,{'outcome':'delivered'})
            with self.assertRaises(ValueError):guard.require_clear(path)
            guard.observed(path,'other')
            with self.assertRaises(ValueError):guard.require_clear(path)
            token=guard.observed(path,'target')
            with self.assertRaises(ValueError):guard.require_clear(path)
            with self.assertRaises(ValueError):guard.confirm(path,'target','wrong','Inspected result')
            guard.confirm(path,'target',token,'Inspected the resulting screen')
            guard.require_clear(path)

    def test_explicit_no_input_result_can_clear(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input.json'
            guard.start(path,'target');guard.finish(path,{'outcome':'user_active'})
            guard.require_clear(path)
            guard.start(path,'target');guard.finish(path,{'outcome':'error','message':'overlay_motion_timeout','mouse_down_sent':False})
            guard.require_clear(path)

    def test_repeat_observation_recovers_token_without_clearing_lock(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input.json'
            guard.start(path,'target');guard.finish(path,{'outcome':'delivered'})
            token=guard.observed(path,'target')
            self.assertEqual(guard.observed(path,'target'),token)
            self.assertIsNone(guard.observed(path,'other'))
            with self.assertRaises(ValueError):guard.require_clear(path)
            guard.confirm(path,'target',token,'Observed the target after the delivered action')
            guard.require_clear(path)

if __name__=='__main__':unittest.main()

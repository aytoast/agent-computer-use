import unittest
from types import SimpleNamespace
import hyperv_game_capture as capture

class CaptureGuardTest(unittest.TestCase):
    def test_valid_foreground_window_bounds(self):
        win=SimpleNamespace(hwnd=10,pid=20,foreground=10,visible=True,minimized=False,
                            title='Agent Workspace',class_name='ApplicationFrameWindow',bounds=(20,30,820,630))
        self.assertEqual(capture.checked_bounds(win,20,(0,0,1920,1080)),(20,30,820,630))

    def test_other_windows_and_offscreen_targets_are_rejected(self):
        base=dict(hwnd=10,pid=20,foreground=10,visible=True,minimized=False,
                  title='Agent Workspace',class_name='ApplicationFrameWindow',bounds=(20,30,820,630))
        for override in ({'foreground':11},{'pid':21},{'visible':False},{'minimized':True},
                         {'bounds':(-1,0,820,630)}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                capture.checked_bounds(SimpleNamespace(**(base|override)),20,(0,0,1920,1080))

if __name__=='__main__': unittest.main()

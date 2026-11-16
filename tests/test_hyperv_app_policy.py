import unittest
from unittest.mock import patch
from hyperv_acu import app_policy as policy

class HyperVAppPolicyTest(unittest.TestCase):
    def test_exact_vm_identity_required(self):
        with patch.object(policy, 'VM_ID', '00000000-0000-0000-0000-000000000001'):
            policy.require_vm('00000000-0000-0000-0000-000000000001')
            with self.assertRaises(ValueError):policy.require_vm('b56a2fd5-3514-4a0a-aa16-36f76ce18f90')

    def test_click_requires_recent_capture_of_same_target(self):
        policy.require_capture({'target':'a','time':100},'a',105)
        policy.require_capture({'target':'a','time':100},'a',129)
        for capture,target,now in ((None,'a',105),({'target':'a','time':100},'b',105),({'target':'a','time':100},'a',131)):
            with self.subTest(capture=capture,target=target,now=now),self.assertRaises(ValueError):
                policy.require_capture(capture,target,now)

    def test_pixel_validation_and_bounds(self):
        self.assertEqual(policy.pixel_point(10,20,100,80),(10,20))
        for x,y in ((-1,0),(100,0),(0,80),(True,3)):
            with self.subTest(x=x,y=y),self.assertRaises(ValueError):policy.pixel_point(x,y,100,80)

    def test_normalized_coordinates_map_to_screenshot_pixels(self):
        self.assertEqual(policy.normalized_point(487,748,855,481),(416,359))
        self.assertEqual(policy.normalized_point(1000,1000,855,481),(854,480))
        with self.assertRaises(ValueError):policy.normalized_point(1001,2,855,481)

    def test_game_and_launcher_background_clicks_are_refused(self):
        for name in ('UnityWndClass','Launcher'):
            with self.subTest(name=name),self.assertRaises(ValueError):policy.require_background_click(name)
        policy.require_background_click('Button')

if __name__=='__main__':unittest.main()

import unittest
from threading import RLock
from types import SimpleNamespace
import hyperv_frame as frame

class FrameTests(unittest.TestCase):
    def agent(self, identities, paused=False):
        values=iter(identities)
        return SimpleNamespace(lock=RLock(),state=SimpleNamespace(paused=paused),identity=lambda target:next(values))

    def test_tracks_only_verified_game_capture(self):
        identity={'hwnd':10,'pid':20,'class':'UnityWndClass'}
        agent=self.agent([identity,identity]);frames={}
        meta,png=frame.capture_tracked(agent,frames,'target',lambda h,p:({'width':100,'height':80},b'png'),lambda:42)
        self.assertEqual(png,b'png');self.assertEqual(frames['target']['size'],(100,80))
        self.assertEqual(frames['target']['captured_at'],42)

    def test_rejects_pause_identity_change_or_invalid_image(self):
        identity={'hwnd':10,'pid':20,'class':'UnityWndClass'}
        for agent,fn in ((self.agent([identity],True),lambda h,p:({'width':1,'height':1},b'p')),
                         (self.agent([identity,identity|{'pid':21}]),lambda h,p:({'width':1,'height':1},b'p')),
                         (self.agent([identity,identity]),lambda h,p:({'width':0,'height':1},b'p'))):
            frames={}
            with self.assertRaises(ValueError):frame.capture_tracked(agent,frames,'target',fn,lambda:42)
            self.assertEqual(frames,{})

if __name__=='__main__':unittest.main()

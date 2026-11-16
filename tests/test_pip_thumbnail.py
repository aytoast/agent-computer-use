import unittest
from unittest.mock import patch
from hyperv_acu.pip_thumbnail import PipThumbnailWindow


class PipThumbnailDockTest(unittest.TestCase):
    def test_auto_collapse_requires_docked_at_edge(self):
        win = PipThumbnailWindow(notch=True, idle_seconds=6.0)
        now = win.last_activity + 10.0
        self.assertTrue(win._should_auto_collapse(now))

        mon = (0, 0, 1920, 1080)
        win._anim_p = 1.0
        win._update_dock_from_rect(400, 300, 720, mon, 1.0, 1.0, 'left', 560.0, 390.0)
        self.assertFalse(win._docked)
        self.assertFalse(win._should_auto_collapse(now))

        win._update_dock_from_rect(1600, 300, 1920, mon, 1.0, 1.0, 'right', 1760.0, 390.0)
        self.assertTrue(win._docked)
        self.assertEqual(win.edge, 'right')
        self.assertTrue(win._should_auto_collapse(now))

    def test_auto_close_when_vm_source_vanishes(self):
        win = PipThumbnailWindow(notch=True)
        with patch('hyperv_acu.pip_thumbnail.find_vmconnect_hwnd', return_value=None):
            self.assertFalse(win._should_auto_close())
            win._had_source = True
            win._guest_connected = True
            win._guest_lost_count = 3
            self.assertFalse(win._should_auto_close())
            win._guest_lost_count = 4
            self.assertTrue(win._should_auto_close())

    def test_double_click_opens_or_focuses_vmconnect(self):
        win = PipThumbnailWindow(title_hint='agent-01', notch=True)
        with patch('hyperv_acu.pip_thumbnail.find_vmconnect_hwnd', return_value=4242), \
             patch('hyperv_acu.pip_thumbnail.win32gui.IsIconic', return_value=True), \
             patch('hyperv_acu.pip_thumbnail.win32gui.ShowWindow') as show_win, \
             patch('hyperv_acu.pip_thumbnail.win32gui.SetForegroundWindow') as fg_win:
            self.assertTrue(win._open_vmconnect())
            show_win.assert_called_once()
            fg_win.assert_called_once_with(4242)

        with patch('hyperv_acu.pip_thumbnail.find_vmconnect_hwnd', return_value=None), \
             patch('hyperv_acu.pip_thumbnail.subprocess.Popen') as popen:
            self.assertTrue(win._open_vmconnect())
            self.assertEqual(popen.call_args[0][0], ['vmconnect.exe', 'localhost', 'agent-01'])


if __name__ == '__main__':
    unittest.main()

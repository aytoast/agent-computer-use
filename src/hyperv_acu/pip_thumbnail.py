"""Native Windows DWM live thumbnail PiP viewer for Hyper-V vmconnect.exe with guest-feed fallback."""
import argparse
import asyncio
import base64
import ctypes
from ctypes import wintypes
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from PIL import Image
import win32api
import win32con
import win32gui

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
from hyperv_acu.bootstrap import ensure_paths
_VM_SETUP = ensure_paths()


DWM_TNP_RECTDESTINATION = 0x00000001
DWM_TNP_RECTSOURCE = 0x00000002
DWM_TNP_OPACITY = 0x00000004
DWM_TNP_VISIBLE = 0x00000008
DWM_TNP_SOURCECLIENTAREAONLY = 0x00000010
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3
DEFAULT_PORT = 18778


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ('dwFlags', wintypes.DWORD),
        ('rcDestination', wintypes.RECT),
        ('rcSource', wintypes.RECT),
        ('opacity', ctypes.c_ubyte),
        ('fVisible', wintypes.BOOL),
        ('fSourceClientAreaOnly', wintypes.BOOL),
    ]


user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
dwmapi = ctypes.WinDLL('dwmapi', use_last_error=True)
msimg32 = ctypes.WinDLL('msimg32', use_last_error=True)
msimg32.AlphaBlend.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
msimg32.AlphaBlend.restype = wintypes.BOOL

dwmapi.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)]
dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long
dwmapi.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
dwmapi.DwmUnregisterThumbnail.restype = ctypes.c_long
dwmapi.DwmQueryThumbnailSourceSize.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.SIZE)]
dwmapi.DwmQueryThumbnailSourceSize.restype = ctypes.c_long
dwmapi.DwmUpdateThumbnailProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.c_long
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.KillTimer.restype = wintypes.BOOL
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetStretchBltMode.restype = ctypes.c_int
gdi32.StretchDIBits.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.DWORD,
]
gdi32.StretchDIBits.restype = ctypes.c_int
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.BitBlt.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def _process_name(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ''
    finally:
        kernel32.CloseHandle(handle)


def find_vmconnect_hwnd(title_hint: str = '') -> int | None:
    """Locate the visible vmconnect.exe window matching title_hint if given."""
    matches = []

    @WNDENUMPROC
    def enum_cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value and _process_name(pid.value) == 'vmconnect.exe':
            title = win32gui.GetWindowText(hwnd) or ''
            matches.append((hwnd, title))
        return True

    user32.EnumWindows(enum_cb, 0)
    if not matches:
        return None
    if title_hint:
        hint = title_hint.lower()
        for hwnd, title in matches:
            if hint in title.lower():
                return hwnd
    return matches[0][0]


def find_notch_rect() -> tuple[int, int, int, int] | None:
    """Find the Windows Agent Trace notch window rect if currently present."""
    found = []

    @WNDENUMPROC
    def enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == 'Windows Agent Trace':
            found.append(win32gui.GetWindowRect(hwnd))
            return False
        return True

    user32.EnumWindows(enum_cb, 0)
    return found[0] if found else None


def find_vmconnect_screen_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return client-relative (left, top, right, bottom) of vmconnect's inner VM display surface."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    try:
        _, _, cw, ch = win32gui.GetClientRect(hwnd)
    except Exception:
        return None
    if cw <= 0 or ch <= 0:
        return None
    client_area = cw * ch
    preferred: list[tuple[int, tuple[int, int, int, int]]] = []
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []

    def _cb(child, _):
        if not win32gui.IsWindowVisible(child):
            return True
        try:
            l, t, r, b = win32gui.GetWindowRect(child)
            sl, st = win32gui.ScreenToClient(hwnd, (l, t))
            sr, sb = win32gui.ScreenToClient(hwnd, (r, b))
        except Exception:
            return True
        sl, st = max(0, sl), max(0, st)
        sr, sb = min(cw, sr), min(ch, sb)
        w, h = sr - sl, sb - st
        area = w * h
        if w <= 0 or h <= 0 or area < client_area * 0.4:
            return True
        cls = win32gui.GetClassName(child)
        if cls in ('IHWindowClass', 'OPWindowClass', 'UIMainClass'):
            preferred.append((area, (sl, st, sr, sb)))
        elif area < client_area * 0.98:
            candidates.append((area, (sl, st, sr, sb)))
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _cb, 0)
    except Exception:
        return None
    if preferred:
        preferred.sort(key=lambda item: item[0])
        return preferred[0][1]
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return None


HANDLE_W = 20
COLLAPSED_H = 72
ANIM_TIMER_ID = 2
ANIM_DURATION = 0.22


class PipThumbnailWindow:
    def __init__(
        self,
        width: int = 320,
        height: int = 180,
        title_hint: str = '',
        opacity: int = 255,
        notch: bool = True,
        idle_seconds: float = 6.0,
        port: int = DEFAULT_PORT,
    ):
        self.width = width
        self.height = height
        self.title_hint = title_hint
        self.opacity = max(30, min(255, opacity))
        self.notch = notch
        self.idle_seconds = idle_seconds
        self.port = port
        self.hwnd = None
        self.src_hwnd = None
        self.thumb = None
        self.visible = True
        self.collapsed = False
        self._docked = True
        self.edge = 'left'
        self.edge_offset = None
        self._allow_close = False
        self._had_source = False
        self._guest_connected = False
        self._guest_lost_count = 0
        self._anim_p = 1.0
        self._anim_from = 1.0
        self._anim_to = 1.0
        self._anim_start = 0.0
        self._animating = False
        self._dragging = False
        self._drag_start_pos = (0, 0)
        self._drag_start_p = 1.0
        self._drag_moved = False
        self._moving = False
        self._move_win_pos = (0, 0)
        self._grab_rel = (0.5, 0.5)
        self.last_activity = time.monotonic()
        self._stop = threading.Event()
        self._sock = None
        self._frame_lock = threading.Lock()
        self._frame_bgr = None
        self._frame_size = (0, 0)
        self._fetcher_thread = None

    def _unregister(self):
        if self.thumb:
            dwmapi.DwmUnregisterThumbnail(self.thumb)
            self.thumb = None
        self.src_hwnd = None

    def _monitor_bounds(self):
        pt = (round(self._move_win_pos[0]), round(self._move_win_pos[1])) if self._moving else (0, 0)
        monitor = win32api.GetMonitorInfo(win32api.MonitorFromPoint(pt, win32con.MONITOR_DEFAULTTONEAREST if self._moving else win32con.MONITOR_DEFAULTTOPRIMARY))
        return monitor['Monitor'], monitor['Work']

    def _position_rect(self, w: int, h: int) -> tuple[int, int]:
        mon, wa = self._monitor_bounds()
        if self.edge == 'top-left':
            return mon[0], mon[1]
        if self.edge == 'top-right':
            return mon[2] - w, mon[1]
        if self.edge == 'top':
            min_x, max_x = mon[0], max(mon[0], mon[2] - w)
            cx = (mon[0] + mon[2]) // 2 if self.edge_offset is None else self.edge_offset
            x = max(min_x, min(max_x, cx - w // 2))
            return x, mon[1]
        min_y, max_y = mon[1], max(mon[1], wa[3] - h)
        cy = (wa[1] + wa[3]) // 2 if self.edge_offset is None else self.edge_offset
        y = max(min_y, min(max_y, cy - h // 2))
        x = (mon[2] - w) if self.edge == 'right' else mon[0]
        return x, y

    def _aspect_ratio(self) -> float:
        if self.thumb:
            src_rect = find_vmconnect_screen_rect(self.src_hwnd) if self.src_hwnd else None
            if src_rect and src_rect[2] > src_rect[0] and src_rect[3] > src_rect[1]:
                return (src_rect[2] - src_rect[0]) / (src_rect[3] - src_rect[1])
            sz = wintypes.SIZE()
            if dwmapi.DwmQueryThumbnailSourceSize(self.thumb, ctypes.byref(sz)) == 0 and sz.cx > 0 and sz.cy > 0:
                return sz.cx / sz.cy
        with self._frame_lock:
            fw, fh = self._frame_size
        if fw > 0 and fh > 0:
            return fw / fh
        return self.width / max(1, self.height)

    def _pan_split(self) -> tuple[float, int, int, float]:
        aspect = self._aspect_ratio()
        full_h = max(COLLAPSED_H, round(self.width / aspect))
        pan_w = max(1, round(COLLAPSED_H * aspect))
        p_pan = COLLAPSED_H / max(1, full_h)
        return aspect, full_h, pan_w, p_pan

    def _handle_width(self, p: float | None = None) -> int:
        return 0

    def _size_for_edge(self, edge: str, p: float) -> tuple[int, int]:
        p = max(0.0, min(1.0, p))
        aspect = self._aspect_ratio()
        min_w = 56
        w = round(min_w + (self.width - min_w) * p)
        h = max(24, round(w / aspect))
        return w, h

    def _rect_for_progress(self, p: float) -> tuple[int, int, int, int]:
        w, h = self._size_for_edge(self.edge, p)
        x, y = self._position_rect(w, h)
        return x, y, w, h

    def _edge_spans(self) -> tuple[float, float]:
        min_w, min_h = self._size_for_edge('left', 0.0)
        full_w, full_h = self._size_for_edge('left', 1.0)
        return max(1.0, (full_w - min_w) / 2.0), max(1.0, (full_h - min_h) / 2.0)

    def _window_edge_params(self, cx_win: float, cy_win: float, full_w: int, full_h: int) -> tuple[float, float, str]:
        mon, _ = self._monitor_bounds()
        x_span, y_span = self._edge_spans()
        over_left = max(0.0, (mon[0] + full_w / 2.0) - cx_win)
        over_right = max(0.0, cx_win - (mon[2] - full_w / 2.0))
        over_top = max(0.0, (mon[1] + full_h / 2.0) - cy_win)
        side = 'right' if over_right > over_left else 'left'
        px = max(0.0, min(1.0, 1.0 - max(over_left, over_right) / x_span))
        py = max(0.0, min(1.0, 1.0 - over_top / y_span))
        return px, py, side

    def _continuous_rect(self, px: float, py: float, side: str, cx_win: float, cy_win: float) -> tuple[int, int, int, int]:
        mon, wa = self._monitor_bounds()
        w, h = self._size_for_edge(side, min(px, py))
        x = max(mon[0], min(mon[2] - w, round(cx_win - w / 2.0)))
        y = max(mon[1], min(wa[3] - h, round(cy_win - h / 2.0)))
        return x, y, w, h

    def _apply_region(self, x: int, y: int, w: int, h: int):
        pass

    def _is_collapse_zone(self, x: int, y: int, r: int, b: int) -> bool:
        if self.collapsed:
            return True
        if self.edge == 'right':
            return x <= HANDLE_W
        if self.edge == 'top':
            return y >= max(0, b - HANDLE_W)
        return x >= max(0, r - HANDLE_W)

    def _snap_point_to_edge(self, cx: int, cy: int):
        mon, _ = self._monitor_bounds()
        d_left = abs(cx - mon[0])
        d_right = abs(mon[2] - cx)
        d_top = abs(cy - mon[1])
        if d_left <= 64 and d_top <= 64:
            self.edge = 'top-left'
            self.edge_offset = None
            return
        if d_right <= 64 and d_top <= 64:
            self.edge = 'top-right'
            self.edge_offset = None
            return
        candidates = [('left', d_left), ('right', d_right), ('top', d_top)]
        self.edge = min(candidates, key=lambda item: item[1])[0]
        self.edge_offset = cx if self.edge == 'top' else cy

    def _snap_to_nearest_edge(self, l: int, t: int, r: int, b: int):
        self._snap_point_to_edge((l + r) // 2, (t + b) // 2)

    def _target_rect(self) -> tuple[int, int, int, int]:
        return self._rect_for_progress(0.0 if self.collapsed else 1.0)

    def _apply_anim_rect(self):
        if not self.hwnd:
            return
        x, y, w, h = self._rect_for_progress(self._anim_p)
        win32gui.SetWindowPos(
            self.hwnd, win32con.HWND_TOPMOST, x, y, w, h,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )
        self._apply_region(x, y, w, h)
        self._update_props()
        win32gui.InvalidateRect(self.hwnd, None, False)

    def _set_collapsed(self, collapsed: bool):
        target_p = 0.0 if collapsed else 1.0
        if collapsed == self.collapsed and not self._animating and abs(self._anim_p - target_p) < 1e-4:
            return
        self.collapsed = collapsed
        if not collapsed:
            self.last_activity = time.monotonic()
        self._anim_from = self._anim_p
        self._anim_to = target_p
        self._anim_start = time.monotonic()
        self._animating = True
        if self.hwnd:
            user32.SetTimer(self.hwnd, ANIM_TIMER_ID, 16, None)

    def _toggle_collapsed(self):
        self._set_collapsed(not self.collapsed)

    def _finish_drag(self, hwnd):
        if not self._dragging and not self._moving:
            return
        self._dragging = False
        self._moving = False
        if win32gui.GetCapture() == hwnd:
            win32gui.ReleaseCapture()
        if not self._drag_moved:
            if self.collapsed:
                self._toggle_collapsed()
            return
        mon, _ = self._monitor_bounds()
        cx, cy = win32gui.GetCursorPos()
        dx = cx - self._drag_start_pos[0]
        dy = cy - self._drag_start_pos[1]
        _, _, full_w, full_h = self._rect_for_progress(1.0)
        cx_win = self._move_win_pos[0] + dx
        cy_win = self._move_win_pos[1] + dy
        px, py, side = self._window_edge_params(cx_win, cy_win, full_w, full_h)
        l, t, r, b = win32gui.GetWindowRect(hwnd) if hwnd else (0, 0, full_w, full_h)
        self._update_dock_from_rect(l, t, r, mon, px, py, side, cx_win, cy_win)
        self._set_collapsed(self._docked and self._anim_p < 0.5)

    def _update_dock_from_rect(self, l: int, t: int, r: int, mon: tuple[int, int, int, int],
                               px: float, py: float, side: str, cx_win: float, cy_win: float):
        at_left = l <= mon[0] + 4
        at_right = r >= mon[2] - 4
        at_top = t <= mon[1] + 4
        if self._anim_p < 1.0 or at_left or at_right or at_top:
            self._docked = True
            if at_left and at_top:
                self.edge = 'top-left'
                self.edge_offset = None
            elif at_right and at_top:
                self.edge = 'top-right'
                self.edge_offset = None
            elif at_top and (py <= px or not (at_left or at_right)):
                self.edge = 'top'
                self.edge_offset = round(cx_win)
            elif at_right or (self._anim_p < 1.0 and side == 'right'):
                self.edge = 'right'
                self.edge_offset = round(cy_win)
            else:
                self.edge = 'left'
                self.edge_offset = round(cy_win)
        else:
            self._docked = False

    def _should_auto_collapse(self, now: float) -> bool:
        return bool(
            self.notch and self._docked and not self.collapsed
            and self.idle_seconds > 0 and now - self.last_activity >= self.idle_seconds
        )

    def _step_animation(self):
        if not self._animating:
            return
        t = min(1.0, (time.monotonic() - self._anim_start) / ANIM_DURATION)
        eased = 1.0 - (1.0 - t) ** 3
        self._anim_p = self._anim_from + (self._anim_to - self._anim_from) * eased
        if t >= 1.0:
            self._anim_p = self._anim_to
            self._animating = False
            if self.hwnd:
                user32.KillTimer(self.hwnd, ANIM_TIMER_ID)
        self._apply_anim_rect()

    def _update_props(self):
        if not self.thumb or not self.hwnd:
            return
        left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
        props = DWM_THUMBNAIL_PROPERTIES()
        props.dwFlags = (
            DWM_TNP_RECTDESTINATION
            | DWM_TNP_OPACITY
            | DWM_TNP_VISIBLE
            | DWM_TNP_SOURCECLIENTAREAONLY
        )
        props.rcDestination = wintypes.RECT(left, top, right, bottom)
        src_rect = find_vmconnect_screen_rect(self.src_hwnd) if self.src_hwnd else None
        if src_rect:
            props.dwFlags |= DWM_TNP_RECTSOURCE
            props.rcSource = wintypes.RECT(*src_rect)
        dim_alpha = min(210, max(0, round(165 * (1.0 - self._anim_p))))
        props.opacity = max(1, round(self.opacity * (1.0 - dim_alpha / 255.0)))
        props.fVisible = True
        props.fSourceClientAreaOnly = True
        dwmapi.DwmUpdateThumbnailProperties(self.thumb, ctypes.byref(props))

    def _request_close(self):
        self._allow_close = True
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)

    def _should_auto_close(self) -> bool:
        if not self._had_source:
            return False
        if self.src_hwnd and win32gui.IsWindow(self.src_hwnd):
            return False
        if find_vmconnect_hwnd(self.title_hint) is not None:
            return False
        if self._guest_connected and self._guest_lost_count < 4:
            return False
        return True

    def _sync_source(self):
        target = self.src_hwnd if (self.src_hwnd and win32gui.IsWindow(self.src_hwnd)) else find_vmconnect_hwnd(self.title_hint)
        if target and win32gui.IsWindow(target):
            self.src_hwnd = target
            self._had_source = True
            if not win32gui.IsIconic(target):
                if not self.thumb and self.hwnd:
                    thumb = wintypes.HANDLE()
                    if dwmapi.DwmRegisterThumbnail(self.hwnd, target, ctypes.byref(thumb)) == 0:
                        self.thumb = thumb
                        self._apply_anim_rect()
                        self._update_props()
                return
        self._unregister()
        self._ensure_guest_fetcher()

    def _ensure_guest_fetcher(self):
        if self._fetcher_thread and self._fetcher_thread.is_alive():
            return
        self._fetcher_thread = threading.Thread(target=self._guest_fetch_loop, daemon=True)
        self._fetcher_thread.start()

    def _guest_fetch_loop(self):
        ensure_paths()
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport
        from hyperv_acu import proxy_config as config
        from hyperv_acu import vm_pool
        token = config.read_guest_token(_VM_SETUP)
        vm_id = os.environ.get('HYPERV_VM_ID', '').strip().lower()
        vm_rec = vm_pool.get_vm(vm_id) if vm_id else None
        expected_mac = (vm_rec or {}).get('mac') or None

        async def stream_frames():
            url = None
            while not self._stop.is_set():
                try:
                    if url is None:
                        url = await asyncio.to_thread(config.resolve_guest_url, None, None, expected_mac)
                    async with Client(StreamableHttpTransport(url, headers={'Authorization': 'Bearer ' + token}), timeout=10) as client:
                        self._had_source = True
                        self._guest_connected = True
                        self._guest_lost_count = 0
                        tools = {t.name for t in await client.list_tools()}
                        while not self._stop.is_set() and not self.thumb:
                            if not self.visible or (self.collapsed and self._anim_p <= 0.05) or (self.hwnd and win32gui.IsIconic(self.hwnd)):
                                await asyncio.sleep(0.2)
                                continue
                            res = None
                            if 'HyperVDesktopScreenshot' in tools:
                                res = await client.call_tool('HyperVDesktopScreenshot', {})
                            if res is not None:
                                png_bytes = None
                                for item in res.content:
                                    if getattr(item, 'type', None) == 'image' and getattr(item, 'data', None):
                                        png_bytes = base64.b64decode(item.data) if isinstance(item.data, str) else item.data
                                        break
                                if png_bytes:
                                    img = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
                                    bgra = img.tobytes('raw', 'BGRA')
                                    with self._frame_lock:
                                        self._frame_bgr = bgra
                                        self._frame_size = img.size
                                    if self.hwnd:
                                        win32gui.PostMessage(self.hwnd, win32con.WM_APP + 2, 0, 0)
                            await asyncio.sleep(0.03)
                except Exception:
                    url = None
                    if self._guest_connected:
                        self._guest_lost_count += 1
                        if self._should_auto_close():
                            self._request_close()
                            return
                    await asyncio.sleep(0.5)

        asyncio.run(stream_frames())

    def _paint_guest_frame(self, hwnd):
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            with self._frame_lock:
                bgra = self._frame_bgr
                w, h = self._frame_size
            l, t, r, b = win32gui.GetClientRect(hwnd)
            cw, ch = max(1, r - l), max(1, b - t)
            back_dc = win32gui.CreateCompatibleDC(hdc)
            back_bmp = win32gui.CreateCompatibleBitmap(hdc, cw, ch)
            old_back = win32gui.SelectObject(back_dc, back_bmp)
            try:
                bg_brush = win32gui.CreateSolidBrush(win32api.RGB(16, 18, 23))
                try:
                    if bgra and w > 0 and h > 0:
                        class BITMAPINFOHEADER(ctypes.Structure):
                            _fields_ = [
                                ('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD),
                            ]
                        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, len(bgra), 0, 0, 0, 0)
                        gdi32.SetStretchBltMode(back_dc, 4)
                        gdi32.StretchDIBits(back_dc, 0, 0, cw, ch, 0, 0, w, h, bgra, ctypes.byref(bmi), 0, 0x00CC0020)
                    else:
                        win32gui.FillRect(back_dc, (0, 0, cw, ch), bg_brush)
                finally:
                    win32gui.DeleteObject(bg_brush)
                if self._anim_p < 1.0:
                    dim_alpha = min(210, max(0, round(165 * (1.0 - self._anim_p))))
                    if dim_alpha > 0:
                        mem_dc = win32gui.CreateCompatibleDC(hdc)
                        bmp = win32gui.CreateCompatibleBitmap(hdc, 1, 1)
                        old_bmp = win32gui.SelectObject(mem_dc, bmp)
                        try:
                            win32gui.FillRect(mem_dc, (0, 0, 1, 1), win32gui.GetStockObject(win32con.BLACK_BRUSH))
                            msimg32.AlphaBlend(back_dc, 0, 0, cw, ch, mem_dc, 0, 0, 1, 1, (dim_alpha << 16))
                        finally:
                            win32gui.SelectObject(mem_dc, old_bmp)
                            win32gui.DeleteObject(bmp)
                            win32gui.DeleteDC(mem_dc)
                gdi32.BitBlt(hdc, 0, 0, cw, ch, back_dc, 0, 0, 0x00CC0020)
            finally:
                win32gui.SelectObject(back_dc, old_back)
                win32gui.DeleteObject(back_bmp)
                win32gui.DeleteDC(back_dc)
        finally:
            win32gui.EndPaint(hwnd, ps)

    def _set_visible(self, show: bool):
        if show:
            self.last_activity = time.monotonic()
            if self.hwnd and win32gui.IsIconic(self.hwnd):
                return
        if not self.hwnd:
            return
        self.visible = True
        self._set_collapsed(not show)

    def _listen_udp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('127.0.0.1', self.port))
        except OSError:
            sock.close()
            return
        sock.settimeout(0.5)
        self._sock = sock
        while not self._stop.is_set():
            try:
                data, _ = sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            cmd = data.decode('utf-8', errors='ignore').strip().lower()
            if cmd == 'show':
                self.last_activity = time.monotonic()
                if self.hwnd:
                    win32gui.PostMessage(self.hwnd, win32con.WM_APP + 1, 1, 0)
            elif cmd == 'hide':
                self.last_activity = 0.0
                if self.hwnd:
                    win32gui.PostMessage(self.hwnd, win32con.WM_APP + 1, 0, 0)
            elif cmd == 'quit' and self.hwnd:
                self._allow_close = True
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)

    def _open_vmconnect(self):
        target = self.src_hwnd if (self.src_hwnd and win32gui.IsWindow(self.src_hwnd)) else find_vmconnect_hwnd(self.title_hint)
        if target:
            if win32gui.IsIconic(target):
                win32gui.ShowWindow(target, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(target)
            return True
        if self.title_hint:
            try:
                subprocess.Popen(['vmconnect.exe', 'localhost', self.title_hint], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                return True
            except Exception:
                return False
        return False

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_APP + 1:
            self._set_visible(bool(wparam))
            return 0
        if msg == win32con.WM_APP + 2:
            if not self._moving and not self._animating:
                _, _, tw, th = self._target_rect()
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                if abs((r - l) - tw) > 2 or abs((b - t) - th) > 2:
                    self._apply_anim_rect()
            win32gui.InvalidateRect(hwnd, None, False)
            return 0
        if msg == win32con.WM_NCHITTEST:
            return win32con.HTCLIENT
        if msg == win32con.WM_LBUTTONDOWN:
            self._animating = False
            user32.KillTimer(hwnd, ANIM_TIMER_ID)
            cx, cy = win32gui.GetCursorPos()
            self._drag_start_pos = (cx, cy)
            self._drag_moved = False
            self._moving = True
            self._dragging = False
            wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
            ww, wh = max(1, wr - wl), max(1, wb - wt)
            self._grab_rel = (max(0.0, min(1.0, (cx - wl) / ww)), max(0.0, min(1.0, (cy - wt) / wh)))
            self._move_win_pos = ((wl + wr) / 2.0, (wt + wb) / 2.0)
            self.last_activity = time.monotonic()
            win32gui.SetCapture(hwnd)
            return 0
        if msg == win32con.WM_MOUSEMOVE:
            if self._moving:
                cx, cy = win32gui.GetCursorPos()
                dx = cx - self._drag_start_pos[0]
                dy = cy - self._drag_start_pos[1]
                if not self._drag_moved and abs(dx) < 4 and abs(dy) < 4:
                    return 0
                self._drag_moved = True
                self.last_activity = time.monotonic()
                mon, wa = self._monitor_bounds()
                _, _, full_w, full_h = self._rect_for_progress(1.0)
                min_w, min_h = self._size_for_edge('left', 0.0)
                cx_win = max(mon[0] + min_w / 2.0, min(mon[2] - min_w / 2.0, self._move_win_pos[0] + dx))
                cy_win = max(mon[1] + min_h / 2.0, min(wa[3] - full_h / 2.0, self._move_win_pos[1] + dy))
                self._move_win_pos = (cx_win - dx, cy_win - dy)
                px, py, side = self._window_edge_params(cx_win, cy_win, full_w, full_h)
                p_min = min(px, py)
                if p_min < 1.0:
                    self._anim_p = p_min
                    if px < 0.6 and py < 0.6:
                        self.edge = 'top-left' if side == 'left' else 'top-right'
                    elif py < px:
                        self.edge = 'top'
                    else:
                        self.edge = side
                    x, y, w, h = self._continuous_rect(px, py, side, cx_win, cy_win)
                    win32gui.SetWindowPos(
                        hwnd, win32con.HWND_TOPMOST, x, y, w, h,
                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                    )
                    self._apply_region(x, y, w, h)
                    win32gui.InvalidateRect(hwnd, None, False)
                elif self._anim_p < 1.0 or self.collapsed:
                    self.collapsed = False
                    self._anim_p = 1.0
                    nx = max(mon[0], min(mon[2] - full_w, round(cx_win - full_w / 2.0)))
                    ny = max(mon[1], min(wa[3] - full_h, round(cy_win - full_h / 2.0)))
                    win32gui.SetWindowPos(
                        hwnd, win32con.HWND_TOPMOST, nx, ny, full_w, full_h,
                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                    )
                    self._apply_region(nx, ny, full_w, full_h)
                    win32gui.InvalidateRect(hwnd, None, False)
                else:
                    nx = max(mon[0], min(mon[2] - full_w, round(cx_win - full_w / 2.0)))
                    ny = max(mon[1], min(wa[3] - full_h, round(cy_win - full_h / 2.0)))
                    win32gui.SetWindowPos(
                        hwnd, win32con.HWND_TOPMOST, nx, ny, 0, 0,
                        win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
                    )
            return 0
        if msg == win32con.WM_LBUTTONUP:
            self._finish_drag(hwnd)
            return 0
        if msg == win32con.WM_CAPTURECHANGED:
            self._finish_drag(hwnd)
            return 0
        if msg == win32con.WM_LBUTTONDBLCLK:
            x = ctypes.c_short(lparam & 0xFFFF).value
            _, _, r, _ = win32gui.GetClientRect(hwnd)
            if not self.collapsed and x < max(0, r - HANDLE_W):
                self._open_vmconnect()
            return 0
        if msg == win32con.WM_CLOSE:
            if not self._allow_close:
                return 0
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_SIZE:
            self._update_props()
            win32gui.InvalidateRect(hwnd, None, False)
            return 0
        if msg == win32con.WM_ERASEBKGND and not self.thumb:
            return 1
        if msg == win32con.WM_PAINT and not self.thumb:
            self._paint_guest_frame(hwnd)
            return 0
        if msg == win32con.WM_TIMER:
            if wparam == ANIM_TIMER_ID:
                self._step_animation()
                return 0
            if self.visible:
                self._sync_source()
                if self._should_auto_close():
                    self._request_close()
                    return 0
                if self._dragging or self._moving:
                    self.last_activity = time.monotonic()
                elif self._should_auto_collapse(time.monotonic()):
                    self._set_collapsed(True)
            return 0
        if msg == win32con.WM_DESTROY:
            self._stop.set()
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
            user32.KillTimer(hwnd, 1)
            user32.KillTimer(hwnd, ANIM_TIMER_ID)
            self._unregister()
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def run(self):
        if self.notch:
            import re
            suffix = re.sub(r'[^A-Za-z0-9_-]+', '_', self.title_hint or 'default')
            self._mutex = kernel32.CreateMutexW(None, False, f'Local\\HyperVPipThumbnail_{suffix}')
            if ctypes.get_last_error() == 183:
                return
        wc = win32gui.WNDCLASS()
        wc.style = win32con.CS_DBLCLKS
        wc.lpfnWndProc = self._wndproc
        wc.lpszClassName = 'HyperVPipThumbnail'
        wc.hbrBackground = win32gui.GetStockObject(win32con.BLACK_BRUSH)
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        atom = win32gui.RegisterClass(wc)

        x, y, w, h = self._target_rect()
        ex_style = win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE
        style = win32con.WS_POPUP | win32con.WS_VISIBLE
        self.hwnd = win32gui.CreateWindowEx(
            ex_style, atom, 'Hyper-V PiP', style, x, y, w, h, 0, 0, 0, None
        )
        pref = ctypes.c_int(DWMWCP_ROUNDSMALL)
        dwmapi.DwmSetWindowAttribute(self.hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref))
        self._apply_region(x, y, w, h)
        if not self.notch:
            self._sync_source()
        if self.notch:
            threading.Thread(target=self._listen_udp, daemon=True).start()
            self._ensure_guest_fetcher()
        user32.SetTimer(self.hwnd, 1, 500, None)
        win32gui.PumpMessages()


def notify_show(port: int = DEFAULT_PORT):
    """Signal the running notch PiP viewer to show, launching it if needed."""
    script = os.path.abspath(__file__)
    launched = False
    try:
        test = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        test.bind(('127.0.0.1', port))
        test.close()
        subprocess.Popen(
            [sys.executable, script, '--notch', '--port', str(port)],
            creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            close_fds=True,
        )
        launched = True
    except OSError:
        pass
    if launched:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                check = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                check.bind(('127.0.0.1', port))
                check.close()
                time.sleep(0.1)
            except OSError:
                time.sleep(0.1)
                break
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.sendto(b'show', ('127.0.0.1', port))
        probe.close()
    except OSError:
        pass


def _enable_dpi_awareness():
    fn = getattr(user32, 'SetProcessDpiAwarenessContext', None)
    if fn:
        fn.argtypes = [wintypes.HANDLE]
        fn.restype = wintypes.BOOL
        if fn(ctypes.c_void_p(-4)):
            return
    if hasattr(user32, 'SetProcessDPIAware'):
        user32.SetProcessDPIAware()


def main():
    _enable_dpi_awareness()
    parser = argparse.ArgumentParser(description='Native DWM PiP thumbnail for Hyper-V vmconnect.exe')
    parser.add_argument('--width', type=int, default=320)
    parser.add_argument('--height', type=int, default=180)
    parser.add_argument('--title', default='')
    parser.add_argument('--opacity', type=int, default=255)
    parser.add_argument('--notch', action='store_true', help='Top-center notch mode: show on VM activity, auto-hide when idle')
    parser.add_argument('--idle-seconds', type=float, default=6.0)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    PipThumbnailWindow(
        width=args.width,
        height=args.height,
        title_hint=args.title,
        opacity=args.opacity,
        notch=args.notch,
        idle_seconds=args.idle_seconds,
        port=args.port,
    ).run()


if __name__ == '__main__':
    main()

"""Authenticated Windows MCP server for the Hyper-V guest virtual machine."""
from pathlib import Path
import os
import sys

ROOT = os.environ.get('HYPERV_GUEST_ROOT', str(Path(__file__).resolve().parent))
if sys.stdout is None:
    sys.stdout = open(os.path.join(ROOT, 'agent.log'), 'a', encoding='utf-8', buffering=1)
if sys.stderr is None:
    sys.stderr = sys.stdout
# Windows multiprocessing imports this module again in each worker. Keep DLL
# directories alive there, but start the MCP server only in the main process.
import site

_CANDIDATE_DLL_DIRS = [
    ROOT + r'\runtime\Scripts',
    ROOT + r'\runtime\Lib\site-packages\pywin32_system32',
    ROOT + r'\runtime\Lib\site-packages\pythonwin',
    os.path.join(sys.prefix, 'Scripts'),
]
for _sp in site.getsitepackages():
    _CANDIDATE_DLL_DIRS.extend([os.path.join(_sp, 'pywin32_system32'), os.path.join(_sp, 'pythonwin')])
_DLL_DIRS = [os.add_dll_directory(p) for p in _CANDIDATE_DLL_DIRS if os.path.isdir(p)]
sys.path.insert(0, ROOT + r'\app')


def main():
    # The scheduled task's console belongs to this service, not the game.
    # Hide it so it cannot intercept guest clicks; logs remain redirected.
    import ctypes
    from ctypes import wintypes
    ctypes.windll.kernel32.GetConsoleWindow.restype = wintypes.HWND
    ctypes.windll.user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    console = ctypes.windll.kernel32.GetConsoleWindow()
    if console:
        ctypes.windll.user32.ShowWindow(console, 0)
    import atexit
    def _restore_system_cursors():
        ctypes.windll.user32.SystemParametersInfoW(0x0057, 0, None, 0)
    _restore_system_cursors()
    atexit.register(_restore_system_cursors)

    from windows_agent.server import create_server
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    tok_path = Path(ROOT) / 'mcp-token.txt'
    if not tok_path.is_file():
        tok_path = Path(ROOT) / 'guest-agent-token.txt'
    token = tok_path.read_text(encoding='utf-8-sig').strip()
    server = create_server(False)
    from fastmcp.utilities.types import Image
    import json
    import uuid
    agent_instance_id = uuid.uuid4().hex

    @server.tool(name='HyperVAgentInstance', annotations={'readOnlyHint': True})
    def hyperv_agent_instance():
        """Identify this guest control-service instance for isolation checks."""
        return {'instance_id': agent_instance_id}

    @server.tool(name='HyperVDesktopScreenshot', annotations={'readOnlyHint': True})
    def hyperv_desktop_screenshot():
        """Capture the entire guest virtual screen with metadata and visible window coordinates."""
        from io import BytesIO
        from PIL import ImageGrab

        def _grab(user32):
            ox, oy, vw, vh = _virtual_bounds(user32)
            image = ImageGrab.grab(all_screens=True)
            stream = BytesIO()
            image.save(stream, format='PNG')
            meta = json.dumps({
                'target_id': 'desktop',
                'width': image.width,
                'height': image.height,
                'origin_x': ox,
                'origin_y': oy,
                'virtual_width': vw,
                'virtual_height': vh,
                'windows': _visible_windows(user32, ox, oy),
            })
            return [meta, Image(data=stream.getvalue(), format='png')]

        return _with_dpi_awareness(_grab)

    def _with_dpi_awareness(fn):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        set_context = getattr(user32, 'SetThreadDpiAwarenessContext', None)
        previous = None
        if set_context:
            set_context.argtypes = [wintypes.HANDLE]
            set_context.restype = wintypes.HANDLE
            previous = set_context(ctypes.c_void_p(-4))
        try:
            return fn(user32)
        finally:
            if previous:
                set_context(previous)

    def _virtual_bounds(user32):
        return (
            user32.GetSystemMetrics(76),
            user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78),
            user32.GetSystemMetrics(79),
        )

    def _send_mouse_events(user32, events):
        import ctypes
        from ctypes import wintypes

        class _MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ('dx', wintypes.LONG),
                ('dy', wintypes.LONG),
                ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.c_size_t),
            ]

        class _INPUT_UNION(ctypes.Union):
            _fields_ = [('mi', _MOUSEINPUT)]

        class _INPUT(ctypes.Structure):
            _fields_ = [('type', wintypes.DWORD), ('union', _INPUT_UNION)]

        arr = (_INPUT * len(events))()
        for idx, (flags, dx, dy, data) in enumerate(events):
            arr[idx].type = 0
            arr[idx].union.mi = _MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
        sent = user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
        if sent != len(events):
            raise OSError(f'SendInput sent {sent}/{len(events)} events (err={ctypes.get_last_error()})')

    def _abs_coords(vx, vy, ox, oy, vw, vh):
        nx = int(round(((vx - ox) * 65535) / max(1, vw - 1)))
        ny = int(round(((vy - oy) * 65535) / max(1, vh - 1)))
        return max(0, min(65535, nx)), max(0, min(65535, ny))

    _BTN_FLAGS = {'left': (0x0002, 0x0004), 'right': (0x0008, 0x0010), 'middle': (0x0020, 0x0040)}

    def _visible_windows(user32, ox, oy):
        import ctypes
        from ctypes import wintypes

        class _RECT(ctypes.Structure):
            _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG), ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

        class _POINT(ctypes.Structure):
            _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]

        windows = []
        enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            wrect = _RECT()
            crect = _RECT()
            pt = _POINT(0, 0)
            if not user32.GetWindowRect(hwnd, ctypes.byref(wrect)):
                return True
            ww, wh = wrect.right - wrect.left, wrect.bottom - wrect.top
            if ww <= 0 or wh <= 0:
                return True
            client = None
            if user32.GetClientRect(hwnd, ctypes.byref(crect)) and user32.ClientToScreen(hwnd, ctypes.byref(pt)):
                cw, ch = crect.right - crect.left, crect.bottom - crect.top
                if cw > 0 and ch > 0:
                    client = {'x': pt.x - ox, 'y': pt.y - oy, 'width': cw, 'height': ch}
            windows.append({
                'hwnd': int(hwnd),
                'title': buf.value,
                'rect': {'x': wrect.left - ox, 'y': wrect.top - oy, 'width': ww, 'height': wh},
                'client_rect': client,
            })
            return True

        user32.EnumWindows(enum_cb(_cb), 0)
        return windows

    def _orig_tool(name):
        t = server.get_tool(name)
        return t.fn if t else None

    _orig_click = _orig_tool('click')
    _orig_move = _orig_tool('move')
    _orig_drag = _orig_tool('drag')
    _orig_scroll = _orig_tool('scroll')
    _orig_key = _orig_tool('Key')
    _orig_type = _orig_tool('Type')

    def _send_key_events(user32, events):
        import ctypes
        from ctypes import wintypes

        class _KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ('wVk', wintypes.WORD),
                ('wScan', wintypes.WORD),
                ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.c_size_t),
            ]

        class _INPUT_UNION(ctypes.Union):
            _fields_ = [('ki', _KEYBDINPUT)]

        class _INPUT(ctypes.Structure):
            _fields_ = [('type', wintypes.DWORD), ('union', _INPUT_UNION)]

        arr = (_INPUT * len(events))()
        for idx, (vk, scan, flags) in enumerate(events):
            arr[idx].type = 1
            arr[idx].union.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        sent = user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
        if sent != len(events):
            raise OSError(f'SendInput sent {sent}/{len(events)} key events (err={ctypes.get_last_error()})')

    _VK_MAP = {
        'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'esc': 0x1B, 'escape': 0x1B,
        'backspace': 0x08, 'space': 0x20, 'delete': 0x2E, 'del': 0x2E,
        'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
        'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
        'ctrl': 0x11, 'control': 0x11, 'alt': 0x12, 'shift': 0x10, 'win': 0x5B, 'super': 0x5B,
    }
    for _i in range(1, 13):
        _VK_MAP[f'f{_i}'] = 0x6F + _i

    @server.tool(name='click')
    def unified_click(point: list[int], target_id: str = '', button: str = 'left', clicks: int = 1, consume_frame: bool = True, guard_input: bool = True):
        """Click coordinates on the full desktop (default) or inside target_id window."""
        if target_id and target_id != 'desktop' and _orig_click:
            return _orig_click(target_id=target_id, point=point, button=button, clicks=clicks, consume_frame=consume_frame, guard_input=guard_input)
        import time
        if button not in _BTN_FLAGS:
            raise ValueError('button must be left, right, or middle')
        if clicks not in (1, 2):
            raise ValueError('clicks must be 1 or 2')
        down_flag, up_flag = _BTN_FLAGS[button]

        def _do(user32):
            ox, oy, vw, vh = _virtual_bounds(user32)
            vx, vy = ox + int(point[0]), oy + int(point[1])
            nx, ny = _abs_coords(vx, vy, ox, oy, vw, vh)
            user32.SetCursorPos(vx, vy)
            events = [(0x0001 | 0x8000 | 0x4000, nx, ny, 0)]
            for idx in range(clicks):
                events.append((down_flag, 0, 0, 0))
                events.append((up_flag, 0, 0, 0))
                if idx + 1 < clicks:
                    _send_mouse_events(user32, events)
                    events = []
                    time.sleep(0.05)
            if events:
                _send_mouse_events(user32, events)
            return {'outcome': 'delivered', 'target_id': 'desktop', 'point': [int(point[0]), int(point[1])], 'button': button, 'clicks': clicks}

        return _with_dpi_awareness(_do)

    @server.tool(name='move')
    def unified_move(point: list[int], target_id: str = '', hover_ms: int = 0, consume_frame: bool = True, guard_input: bool = True):
        """Move the guest cursor on the full desktop (default) or inside target_id window."""
        if target_id and target_id != 'desktop' and _orig_move:
            return _orig_move(target_id=target_id, point=point, hover_ms=hover_ms, consume_frame=consume_frame, guard_input=guard_input)
        import time

        def _do(user32):
            ox, oy, vw, vh = _virtual_bounds(user32)
            vx, vy = ox + int(point[0]), oy + int(point[1])
            nx, ny = _abs_coords(vx, vy, ox, oy, vw, vh)
            user32.SetCursorPos(vx, vy)
            _send_mouse_events(user32, [(0x0001 | 0x8000 | 0x4000, nx, ny, 0)])
            if hover_ms > 0:
                time.sleep(hover_ms / 1000.0)
            return {'outcome': 'delivered', 'target_id': 'desktop', 'point': [int(point[0]), int(point[1])], 'hover_ms': hover_ms}

        return _with_dpi_awareness(_do)

    @server.tool(name='drag')
    def unified_drag(start_point: list[int], end_point: list[int], target_id: str = '', button: str = 'left', duration_ms: int = 250, consume_frame: bool = True, guard_input: bool = True):
        """Drag across the full desktop (default) or inside target_id window."""
        if target_id and target_id != 'desktop' and _orig_drag:
            return _orig_drag(target_id=target_id, start_point=start_point, end_point=end_point, button=button, duration_ms=duration_ms, consume_frame=consume_frame, guard_input=guard_input)
        import time
        if button not in _BTN_FLAGS:
            raise ValueError('button must be left, right, or middle')
        down_flag, up_flag = _BTN_FLAGS[button]

        def _do(user32):
            ox, oy, vw, vh = _virtual_bounds(user32)
            sx, sy = ox + int(start_point[0]), oy + int(start_point[1])
            ex, ey = ox + int(end_point[0]), oy + int(end_point[1])
            snx, sny = _abs_coords(sx, sy, ox, oy, vw, vh)
            user32.SetCursorPos(sx, sy)
            _send_mouse_events(user32, [(0x0001 | 0x8000 | 0x4000, snx, sny, 0), (down_flag, 0, 0, 0)])
            steps = max(2, min(30, duration_ms // 15 if duration_ms > 0 else 2))
            step_sleep = (duration_ms / 1000.0) / steps if duration_ms > 0 else 0
            for step in range(1, steps + 1):
                t = step / steps
                cx = int(round(sx + (ex - sx) * t))
                cy = int(round(sy + (ey - sy) * t))
                cnx, cny = _abs_coords(cx, cy, ox, oy, vw, vh)
                user32.SetCursorPos(cx, cy)
                _send_mouse_events(user32, [(0x0001 | 0x8000 | 0x4000, cnx, cny, 0)])
                if step_sleep > 0:
                    time.sleep(step_sleep)
            _send_mouse_events(user32, [(up_flag, 0, 0, 0)])
            return {'outcome': 'delivered', 'target_id': 'desktop', 'start_point': [int(start_point[0]), int(start_point[1])], 'end_point': [int(end_point[0]), int(end_point[1])], 'button': button}

        return _with_dpi_awareness(_do)

    @server.tool(name='scroll')
    def unified_scroll(dx: int = 0, dy: int = 0, target_id: str = '', point: list[int] | None = None, consume_frame: bool = True, guard_input: bool = True):
        """Scroll vertically or horizontally on the full desktop (default) or inside target_id window."""
        if target_id and target_id != 'desktop' and _orig_scroll:
            return _orig_scroll(target_id=target_id, dx=dx, dy=dy, point=point, consume_frame=consume_frame, guard_input=guard_input)

        def _do(user32):
            events = []
            if point is not None:
                ox, oy, vw, vh = _virtual_bounds(user32)
                vx, vy = ox + int(point[0]), oy + int(point[1])
                nx, ny = _abs_coords(vx, vy, ox, oy, vw, vh)
                user32.SetCursorPos(vx, vy)
                events.append((0x0001 | 0x8000 | 0x4000, nx, ny, 0))
            if dy:
                events.append((0x0800, 0, 0, int(dy) & 0xFFFFFFFF))
            if dx:
                events.append((0x01000, 0, 0, int(dx) & 0xFFFFFFFF))
            if events:
                _send_mouse_events(user32, events)
            return {'outcome': 'delivered', 'target_id': 'desktop', 'dx': dx, 'dy': dy, 'point': point}

        return _with_dpi_awareness(_do)

    @server.tool(name='Key')
    def unified_key(key: str, target_id: str = '', point: list[int] | None = None, element_token: str | None = None):
        """Send a key or key chord (e.g. 'enter', 'ctrl+c') desktop-wide (default) or to target_id window."""
        if target_id and target_id != 'desktop' and _orig_key:
            return _orig_key(target_id=target_id, key=key, point=point, element_token=element_token)
        parts = [p.strip().lower() for p in key.split('+') if p.strip()]
        if not parts:
            raise ValueError('key must be non-empty')

        def _resolve_vk(tok):
            if tok in _VK_MAP:
                return _VK_MAP[tok]
            if len(tok) == 1:
                vk = ctypes.WinDLL('user32').VkKeyScanW(ord(tok)) & 0xFF
                if vk != 0xFF:
                    return vk
            raise ValueError(f'Unsupported key: {tok}')

        import ctypes
        vks = [_resolve_vk(p) for p in parts]

        def _do(user32):
            if point is not None:
                unified_click(point=point)
            events = [(vk, 0, 0) for vk in vks] + [(vk, 0, 0x0002) for vk in reversed(vks)]
            _send_key_events(user32, events)
            return {'outcome': 'delivered', 'target_id': 'desktop', 'key': key}

        return _with_dpi_awareness(_do)

    @server.tool(name='Type')
    def unified_type(text: str, target_id: str = '', element_token: str | None = None, point: list[int] | None = None, clear: bool = True):
        """Type Unicode text desktop-wide (default) or into target_id window."""
        if target_id and target_id != 'desktop' and _orig_type:
            return _orig_type(target_id=target_id, text=text, element_token=element_token, point=point, clear=clear)

        def _do(user32):
            if point is not None:
                unified_click(point=point)
            events = []
            for ch in text:
                code = ord(ch)
                events.append((0, code, 0x0004))
                events.append((0, code, 0x0004 | 0x0002))
            if events:
                _send_key_events(user32, events)
            return {'outcome': 'delivered', 'target_id': 'desktop', 'length': len(text)}

        return _with_dpi_awareness(_do)

    @server.tool(name='ExecPowerShell')
    def exec_powershell(command: str, timeout_seconds: int = 30, working_directory: str | None = None):
        """Execute a PowerShell command inside the guest and await its output."""
        import base64
        import subprocess
        if not isinstance(command, str) or not command.strip():
            raise ValueError('PowerShell command must be a non-empty string')
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
            raise ValueError('timeout_seconds must be an integer between 1 and 300')
        if working_directory is not None and (not isinstance(working_directory, str) or not working_directory.strip()):
            raise ValueError('working_directory must be a non-empty string when provided')
        encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
        args = [
            'powershell.exe',
            '-NoProfile',
            '-NonInteractive',
            '-ExecutionPolicy',
            'Bypass',
            '-EncodedCommand',
            encoded,
        ]
        try:
            proc = subprocess.run(
                args,
                cwd=working_directory,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout_seconds,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            return {
                'ok': proc.returncode == 0,
                'returncode': proc.returncode,
                'stdout': proc.stdout,
                'stderr': proc.stderr,
                'timed_out': False,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                'ok': False,
                'returncode': None,
                'stdout': exc.stdout.decode('utf-8', errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or ''),
                'stderr': exc.stderr.decode('utf-8', errors='replace') if isinstance(exc.stderr, bytes) else (exc.stderr or ''),
                'timed_out': True,
            }

    server.auth = StaticTokenVerifier({token: {'client_id': 'hyperv-local', 'scopes': []}})
    server.run(transport='streamable-http', host='0.0.0.0', port=18777)


if __name__ == '__main__':
    main()

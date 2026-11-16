"""Capture a visible Hyper-V guest window without moving input or changing focus."""
from types import SimpleNamespace

def checked_bounds(window, expected_pid, screen):
    if (window.hwnd != window.foreground or window.pid != expected_pid or
        not window.visible or window.minimized):
        raise ValueError('The target window is not foreground and eligible')
    left, top, right, bottom = window.bounds
    sl, st, sr, sb = screen
    if not (sl <= left < right <= sr and st <= top < bottom <= sb):
        raise ValueError('The full game client area is not visible on the guest display')
    return window.bounds

def capture_game(hwnd, expected_pid):
    import ctypes
    from ctypes import wintypes
    from io import BytesIO
    import win32api
    import win32con
    import win32gui
    import win32process
    from PIL import ImageGrab

    user32=ctypes.WinDLL('user32',use_last_error=True)
    set_context=getattr(user32,'SetThreadDpiAwarenessContext',None)
    previous=None
    if set_context:
        set_context.argtypes=[wintypes.HANDLE];set_context.restype=wintypes.HANDLE
        previous=set_context(ctypes.c_void_p(-4))
    def observe():
        x,y=win32gui.ClientToScreen(hwnd,(0,0))
        l,t,r,b=win32gui.GetClientRect(hwnd)
        return SimpleNamespace(hwnd=hwnd,pid=win32process.GetWindowThreadProcessId(hwnd)[1],
            foreground=win32gui.GetForegroundWindow(),visible=win32gui.IsWindowVisible(hwnd),
            minimized=win32gui.IsIconic(hwnd),title=win32gui.GetWindowText(hwnd),
            class_name=win32gui.GetClassName(hwnd),bounds=(x,y,x+r-l,y+b-t))
    try:
        sx=win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        sy=win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        screen=(sx,sy,sx+win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN),
                sy+win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN))
        before=observe();bounds=checked_bounds(before,expected_pid,screen)
        image=ImageGrab.grab(bbox=bounds,all_screens=True)
        after=observe()
        if checked_bounds(after,expected_pid,screen)!=bounds:
            raise ValueError('Game window changed during capture')
        if image.size!=(bounds[2]-bounds[0],bounds[3]-bounds[1]):
            raise ValueError('Unexpected capture dimensions')
        stream=BytesIO();image.save(stream,format='PNG')
        return {'hwnd':hwnd,'pid':expected_pid,'bounds':bounds,'width':image.width,'height':image.height},stream.getvalue()
    finally:
        if set_context and previous: set_context(previous)


def capture_desktop():
    """Capture the full guest virtual screen as PNG bytes and metadata."""
    import ctypes
    from ctypes import wintypes
    from io import BytesIO
    import win32api
    import win32con
    from PIL import ImageGrab

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    set_context = getattr(user32, 'SetThreadDpiAwarenessContext', None)
    previous = None
    if set_context:
        set_context.argtypes = [wintypes.HANDLE]
        set_context.restype = wintypes.HANDLE
        previous = set_context(ctypes.c_void_p(-4))
    try:
        sx = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        sy = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        sw = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        sh = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
        bounds = (sx, sy, sx + sw, sy + sh)
        image = ImageGrab.grab(bbox=bounds, all_screens=True)
        stream = BytesIO()
        image.save(stream, format='PNG')
        return {'bounds': bounds, 'width': image.width, 'height': image.height}, stream.getvalue()
    finally:
        if set_context and previous:
            set_context(previous)

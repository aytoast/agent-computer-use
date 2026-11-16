"""Detect whether the user is interacting with a VM window."""

def vm_ready(state):
    return bool(isinstance(state,dict) and
                (state.get('vm_foreground') is False or
                 (state.get('vm_foreground') is True and state.get('tick') is not None and state.get('idle_seconds',0)>=10)))

def vm_uninterrupted(samples):
    if not samples or not all(vm_ready(s) for s in samples):return False
    first=samples[0]
    return all(not (s.get('vm_foreground') and not first.get('vm_foreground')) and
               not ((first.get('vm_foreground') or s.get('vm_foreground')) and s.get('tick')!=first.get('tick'))
               for s in samples)

def read_host_state():
    import ctypes
    from ctypes import wintypes
    import psutil
    import win32gui
    import win32process

    foreground=win32gui.GetForegroundWindow()
    if not foreground:
        user=ctypes.WinDLL('user32',use_last_error=True)
        desktop=user.OpenInputDesktop(0,False,0x0001)
        if not desktop:raise OSError('Host foreground window is unavailable')
        user.CloseDesktop(desktop)
        return {'vm_foreground':False,'foreground':0}
    _,pid=win32process.GetWindowThreadProcessId(foreground)
    try:vm_foreground=psutil.Process(pid).name().lower()=='vmconnect.exe'
    except (psutil.Error,OSError):raise OSError('Host foreground process is unavailable') from None
    if not vm_foreground:return {'vm_foreground':False,'foreground':foreground}
    class LastInput(ctypes.Structure):
        _fields_=[('cbSize',wintypes.UINT),('dwTime',wintypes.DWORD)]
    user=ctypes.WinDLL('user32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    user.GetLastInputInfo.argtypes=[ctypes.POINTER(LastInput)]
    user.GetLastInputInfo.restype=wintypes.BOOL
    kernel.GetTickCount64.restype=ctypes.c_ulonglong
    last=LastInput(ctypes.sizeof(LastInput),0)
    if not user.GetLastInputInfo(ctypes.byref(last)):raise OSError('Host input state is unavailable')
    idle=(((kernel.GetTickCount64()&0xffffffff)-last.dwTime)&0xffffffff)/1000
    return {'vm_foreground':True,'foreground':foreground,'tick':last.dwTime,'idle_seconds':idle}

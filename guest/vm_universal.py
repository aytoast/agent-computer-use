"""Guarded, separately authorized foreground support within the VM."""


def normalized_to_client(point,size):
    """Map a 0..1000 model point to the captured client area, inclusive."""
    if (not isinstance(point,(list,tuple)) or len(point)!=2
            or any(type(v) is not int or not 0<=v<=1000 for v in point)
            or not isinstance(size,(list,tuple)) or len(size)!=2
            or any(type(v) is not int or v<1 for v in size)):
        return None
    return [round(point[0]*(size[0]-1)/1000),round(point[1]*(size[1]-1)/1000)]


def thread_diagnostics(agent):
    """Read lock availability and Python stack locations, without locals."""
    import os,sys,threading
    acquired=agent.lock.acquire(blocking=False)
    if acquired:agent.lock.release()
    frames=sys._current_frames()
    threads=[]
    for thread in threading.enumerate():
        frame=frames.get(thread.ident)
        stack=[]
        while frame:
            stack.append({'file':os.path.basename(frame.f_code.co_filename),
                          'function':frame.f_code.co_name,'line':frame.f_lineno})
            frame=frame.f_back
        threads.append({'name':thread.name,'alive':thread.is_alive(),'stack':stack[:16]})
    return {'agent_lock_available':acquired,'paused':agent.state.paused,'threads':threads}


def post_move_target_guard(backend,point,own_tick):
    """Allow the down event only when user/focus are unchanged and point hits target."""
    state=backend.state()
    safe=bool(own_tick is not None and not state.get('paused') and not state.get('held')
              and state.get('tick')==own_tick and backend.validate_target()
              and backend.is_target_foreground())
    if not safe:return {'allowed':False,'reason':'user_or_focus_changed'}
    if not backend.point_is_target(point):return {'allowed':False,'reason':'target_obscured'}
    return {'allowed':True}


def probe_cursor(backend):
    """Move the guest cursor 16 px and restore it, but never during takeover."""
    state=backend.state()
    if state.get('paused'):
        return {'outcome':'paused'}
    if state.get('desktop')!='Default':
        return {'outcome':'protected_desktop'}
    if state.get('held') or state.get('idle_ms',0)<10000 or state.get('tick') is None:
        return {'outcome':'user_active'}
    before=backend.position()
    width,height=backend.screen()
    if len(before)!=2 or width<32 or height<1 or not (0<=before[0]<width and 0<=before[1]<height):
        return {'outcome':'invalid_geometry'}
    x=before[0]+16 if before[0]+16<width else before[0]-16
    target=[x,before[1]]
    error=backend.move(target)
    if error:
        return {'outcome':'error','error_code':error,'before':before,'target':target}
    backend.wait()
    after_move=backend.position()
    current=backend.state()
    if current.get('paused') or current.get('held') or current.get('tick')!=state['tick'] or after_move!=target:
        return {'outcome':'interrupted','before':before,'target':target,'actual':after_move}
    error=backend.move(before)
    if error:
        return {'outcome':'restore_error','error_code':error,'before':before,'target':target}
    after=backend.position()
    return {'outcome':'moved_and_restored' if after==before else 'restore_unverified',
            'before':before,'target':target,'after':after}
class _Win32ProbeBackend:
    def __init__(self,agent):
        import ctypes
        from ctypes import wintypes
        self.ctypes=ctypes;self.wintypes=wintypes;self.agent=agent
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.u.SetCursorPos.argtypes=[wintypes.INT,wintypes.INT]
        self.u.SetCursorPos.restype=wintypes.BOOL
        self.u.GetCursorPos.argtypes=[ctypes.POINTER(wintypes.POINT)]
        self.u.GetCursorPos.restype=wintypes.BOOL
        self.u.OpenInputDesktop.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        self.u.OpenInputDesktop.restype=wintypes.HANDLE
        self.u.GetUserObjectInformationW.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
        self.u.CloseDesktop.argtypes=[wintypes.HANDLE]
        self.k.GetTickCount64.restype=ctypes.c_ulonglong

    def state(self):
        from windows_agent import native
        tick=native.last_input_tick()
        elapsed=((self.k.GetTickCount64()&0xffffffff)-tick)&0xffffffff if tick is not None else 0
        held=any(self.u.GetAsyncKeyState(key)&0x8000 for key in range(1,256))
        desk=self.u.OpenInputDesktop(0,False,0x0001)
        name=None
        if desk:
            try:
                buf=self.ctypes.create_unicode_buffer(256);needed=self.wintypes.DWORD()
                if self.u.GetUserObjectInformationW(desk,2,buf,self.ctypes.sizeof(buf),self.ctypes.byref(needed)):
                    name=buf.value
            finally:self.u.CloseDesktop(desk)
        return {'paused':self.agent.state.paused,'idle_ms':elapsed,'held':held,'tick':tick,'desktop':name}

    def position(self):
        point=self.wintypes.POINT()
        if not self.u.GetCursorPos(self.ctypes.byref(point)):
            raise OSError(self.ctypes.get_last_error(),'GetCursorPos failed')
        return [point.x,point.y]

    def screen(self):return [self.u.GetSystemMetrics(0),self.u.GetSystemMetrics(1)]

    def move(self,point):
        self.ctypes.set_last_error(0)
        if self.u.SetCursorPos(*point):return 0
        return self.ctypes.get_last_error() or -1

    def wait(self):
        import time
        time.sleep(.5)


def _foreground_preflight(backend,points,frame):
    """Validate guest state, frame freshness, target geometry, and point bounds."""
    state=backend.state()
    if state.get('paused'):return None,{'outcome':'paused'}
    if state.get('desktop')!='Default':return None,{'outcome':'protected_desktop'}
    if state.get('held') or state.get('idle_ms',0)<5000 or state.get('tick') is None:
        return None,{'outcome':'user_active'}
    if not frame.get('valid') or not isinstance(frame.get('age'),(int,float)) or not 0<=frame['age']<=90:
        return None,{'outcome':'stale_frame'}
    if not backend.validate_target():return None,{'outcome':'invalid_target'}
    width,height=backend.geometry()
    if tuple(frame.get('size',()))!=(width,height):return None,{'outcome':'display_changed'}
    for pt in points:
        if not isinstance(pt,list) or len(pt)!=2 or any(type(x) is not int for x in pt):
            return None,{'outcome':'invalid_point'}
        if not (0<=pt[0]<width and 0<=pt[1]<height):return None,{'outcome':'invalid_point'}
    if not backend.activate():return None,{'outcome':'foreground_unavailable'}
    current=backend.state()
    if current.get('paused') or current.get('held') or current.get('tick')!=state['tick']:
        return None,{'outcome':'interrupted'}
    return state,None


def _build_ai_hcursor():
    """Create a no-tail arrowhead Win32 HCURSOR, or return None on failure."""
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image, ImageDraw
        w,h=32,32
        img=Image.new('RGBA',(w,h),(0,0,0,0))
        draw=ImageDraw.Draw(img)
        draw.polygon([(1,1),(22,12),(11,22)],fill=(66,178,229,255),outline=(235,248,255,255))
        bgra=img.tobytes('raw','BGRA')
        gdi32=ctypes.WinDLL('gdi32',use_last_error=True)
        user32=ctypes.WinDLL('user32',use_last_error=True)
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_=[('biSize',wintypes.DWORD),('biWidth',wintypes.LONG),('biHeight',wintypes.LONG),
                      ('biPlanes',wintypes.WORD),('biBitCount',wintypes.WORD),('biCompression',wintypes.DWORD),
                      ('biSizeImage',wintypes.DWORD),('biXPelsPerMeter',wintypes.LONG),
                      ('biYPelsPerMeter',wintypes.LONG),('biClrUsed',wintypes.DWORD),('biClrImportant',wintypes.DWORD)]
        class ICONINFO(ctypes.Structure):
            _fields_=[('fIcon',wintypes.BOOL),('xHotspot',wintypes.DWORD),('yHotspot',wintypes.DWORD),
                      ('hbmMask',wintypes.HBITMAP),('hbmColor',wintypes.HBITMAP)]
        bmi=BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER),w,-h,1,32,0,len(bgra),0,0,0,0)
        bits=ctypes.c_void_p()
        hdc=user32.GetDC(None)
        color_bmp=gdi32.CreateDIBSection(hdc,ctypes.byref(bmi),0,ctypes.byref(bits),None,0)
        user32.ReleaseDC(None,hdc)
        if not color_bmp or not bits.value:
            if color_bmp:gdi32.DeleteObject(color_bmp)
            return None
        ctypes.memmove(bits.value,bgra,len(bgra))
        mask_bmp=gdi32.CreateBitmap(w,h,1,1,None)
        info=ICONINFO(False,1,1,mask_bmp,color_bmp)
        hcur=user32.CreateIconIndirect(ctypes.byref(info))
        gdi32.DeleteObject(color_bmp)
        if mask_bmp:gdi32.DeleteObject(mask_bmp)
        return hcur or None
    except Exception:
        return None


class _SystemCursorGuard:
    """Temporarily set the Windows system cursor during an action, then restore."""
    def __enter__(self):
        import ctypes
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        hcur=_build_ai_hcursor()
        if not hcur:
            loaded=self.u.LoadCursorW(None,32650)
            hcur=self.u.CopyIcon(loaded) if loaded else None
        if hcur:
            for cid in (32512,32649):
                copy=self.u.CopyIcon(hcur)
                if copy:
                    if not self.u.SetSystemCursor(copy,cid):
                        self.u.DestroyIcon(copy)
            self.u.DestroyIcon(hcur)
        return self

    def __exit__(self,*args):
        self.u.SystemParametersInfoW(0x0057,0,None,0)


def _run_foreground_guarded(backend,point,frame,extra_points,action_fn,extra_result=None):
    """Run one guarded foreground input action with temporary system cursor swap."""
    state,blocked=_foreground_preflight(backend,[point,*extra_points],frame)
    if blocked:return blocked
    if not backend.validate_target():return {'outcome':'invalid_target'}
    if not backend.point_is_target(point):
        return {'outcome':'target_obscured','stage':'before_input','hit':getattr(backend,'last_hit',None)}
    current=backend.state()
    if current.get('paused') or current.get('held') or current.get('tick')!=state['tick']:
        return {'outcome':'interrupted'}
    with _SystemCursorGuard():
        ok=action_fn()
    if not ok:
        return {'outcome':'input_outcome_unknown','verified':False,
                'stage':getattr(backend,'input_stage',None),
                'input_error':getattr(backend,'input_error',None),
                'mouse_down_sent':getattr(backend,'mouse_down_sent',None),
                'hit':getattr(backend,'last_hit',None)}
    result={'outcome':'delivered','verified':False,'requires_postcondition_capture':True,'point':point}
    if extra_result:result.update(extra_result)
    return result


def foreground_click(backend,point,frame,button='left',clicks=1):
    """Deliver at most one separately authorized guest click from a fresh frame."""
    return _run_foreground_guarded(
        backend,point,frame,[],
        lambda:backend.send_click(point,button=button,clicks=clicks),
        {'button':button,'clicks':clicks},
    )


def foreground_move(backend,point,frame,hover_ms=0):
    """Move guest cursor to a point and optionally hover."""
    return _run_foreground_guarded(
        backend,point,frame,[],
        lambda:backend.send_move(point,hover_ms=hover_ms),
        {'hover_ms':hover_ms},
    )


def foreground_drag(backend,start_point,end_point,frame,button='left',duration_ms=250):
    """Drag guest cursor from start_point to end_point."""
    return _run_foreground_guarded(
        backend,start_point,frame,[end_point],
        lambda:backend.send_drag(start_point,end_point,button=button,duration_ms=duration_ms),
        {'start_point':start_point,'end_point':end_point,'button':button,'duration_ms':duration_ms},
    )


def foreground_scroll(backend,frame,dx=0,dy=0,point=None):
    """Scroll horizontally/vertically in the foreground guest window."""
    anchor=point if point is not None else [max(1,backend.geometry()[0]//2),max(1,backend.geometry()[1]//2)]
    return _run_foreground_guarded(
        backend,anchor,frame,[],
        lambda:backend.send_scroll(dx=dx,dy=dy,point=point),
        {'dx':dx,'dy':dy},
    )


class _Win32ClickBackend(_Win32ProbeBackend):
    def __init__(self,agent,identity):
        super().__init__(agent)
        self.identity=identity
        self.hwnd=identity['hwnd']

    def validate_target(self):
        from windows_agent import native
        try:native.validate(self.identity);return True
        except Exception:return False

    def geometry(self):
        from windows_agent import native
        _,width,height=native.client_geometry(self.hwnd)
        return (width,height)

    def activate(self):
        import win32gui, win32process
        if win32gui.IsIconic(self.hwnd):return False
        if win32gui.GetForegroundWindow()!=self.hwnd:
            # Attach only the guest's input threads while requesting focus.
            # User activity is checked immediately after returning.
            current=self.k.GetCurrentThreadId()
            threads=set()
            for window in (win32gui.GetForegroundWindow(),self.hwnd):
                if window:
                    thread,_=win32process.GetWindowThreadProcessId(window)
                    if thread and thread!=current:threads.add(thread)
            attached=[]
            try:
                for thread in threads:
                    if self.u.AttachThreadInput(current,thread,True):attached.append(thread)
                win32gui.BringWindowToTop(self.hwnd)
                win32gui.SetForegroundWindow(self.hwnd)
            except Exception:return False
            finally:
                for thread in reversed(attached):self.u.AttachThreadInput(current,thread,False)
        return win32gui.GetForegroundWindow()==self.hwnd

    def point_is_target(self,point):
        import win32gui,win32process
        from windows_agent import native
        origin,_,_=native.client_geometry(self.hwnd)
        found=win32gui.WindowFromPoint((origin[0]+point[0],origin[1]+point[1]))
        _,pid=win32process.GetWindowThreadProcessId(found) if found else (0,0)
        self.last_hit={'hwnd':found,'pid':pid,'class':win32gui.GetClassName(found) if found else '',
                       'title':win32gui.GetWindowText(found) if found else ''}
        return bool(found==self.hwnd or (found and win32gui.IsChild(self.hwnd,found)))

    def is_target_foreground(self):
        import win32gui
        return win32gui.GetForegroundWindow()==self.hwnd

    def _setup_mouse_emitter(self):
        c=self.ctypes;w=self.wintypes;u=self.u
        self.input_stage='geometry';self.input_error=None;self.mouse_down_sent=False
        left,top=u.GetSystemMetrics(76),u.GetSystemMetrics(77)
        width,height=u.GetSystemMetrics(78),u.GetSystemMetrics(79)
        class Mouse(c.Structure):
            _fields_=[('dx',w.LONG),('dy',w.LONG),('data',w.DWORD),('flags',w.DWORD),('time',w.DWORD),('extra',c.c_size_t)]
        class Keyboard(c.Structure):
            _fields_=[('vk',w.WORD),('scan',w.WORD),('flags',w.DWORD),('time',w.DWORD),('extra',c.c_size_t)]
        class Hardware(c.Structure):_fields_=[('msg',w.DWORD),('low',w.WORD),('high',w.WORD)]
        class Union(c.Union):_fields_=[('mouse',Mouse),('keyboard',Keyboard),('hardware',Hardware)]
        class Input(c.Structure):_fields_=[('kind',w.DWORD),('value',Union)]
        u.SendInput.argtypes=[w.UINT,c.POINTER(Input),c.c_int];u.SendInput.restype=w.UINT
        def to_abs(px,py):
            return round((px-left)*65535/(width-1)),round((py-top)*65535/(height-1))
        def emit(flags,dx=0,dy=0,data=0):
            event=Input();event.kind=0;event.value.mouse=Mouse(dx,dy,data,flags,0,0)
            c.set_last_error(0);sent=u.SendInput(1,c.byref(event),c.sizeof(Input))==1
            if not sent:self.input_error=c.get_last_error()
            return sent
        return left,top,width,height,to_abs,emit

    def _move_and_guard(self,point,emit,to_abs,origin):
        import time,win32gui
        from windows_agent import native
        x,y=origin[0]+point[0],origin[1]+point[1]
        self.input_stage='initial_window_check'
        if not self.validate_target() or win32gui.GetForegroundWindow()!=self.hwnd:return None
        self.input_stage='move'
        ax,ay=to_abs(x,y)
        if not emit(0xC001,ax,ay):return None
        own_tick=native.last_input_tick();time.sleep(.08)
        self.input_stage='post_move_check'
        return post_move_target_guard(self,point,own_tick)

    def send_click(self,point,button='left',clicks=1):
        import time
        from windows_agent import native
        buttons={'left':(0x0002,0x0004),'right':(0x0008,0x0010),'middle':(0x0020,0x0040)}
        if button not in buttons or clicks not in (1,2):return False
        down_flag,up_flag=buttons[button]
        left,top,width,height,to_abs,emit=self._setup_mouse_emitter()
        origin,_,_=native.client_geometry(self.hwnd)
        x,y=origin[0]+point[0],origin[1]+point[1]
        if width<2 or height<2 or not (left<=x<left+width and top<=y<top+height):return False
        guard=self._move_and_guard(point,emit,to_abs,origin)
        if guard is None:return False
        if not guard['allowed']:
            self.input_stage=guard.get('reason','post_move_check')
            return False
        for idx in range(clicks):
            self.input_stage='mouse_down';down=emit(down_flag)
            if not down:return False
            self.mouse_down_sent=True
            try:
                time.sleep(.06)
            finally:
                self.input_stage='mouse_up'
                released=emit(up_flag)
            if not released:return False
            if idx+1<clicks:time.sleep(.05)
        return True

    def send_move(self,point,hover_ms=0):
        import time
        from windows_agent import native
        left,top,width,height,to_abs,emit=self._setup_mouse_emitter()
        origin,_,_=native.client_geometry(self.hwnd)
        x,y=origin[0]+point[0],origin[1]+point[1]
        if width<2 or height<2 or not (left<=x<left+width and top<=y<top+height):return False
        guard=self._move_and_guard(point,emit,to_abs,origin)
        if guard is None:return False
        if not guard['allowed']:
            self.input_stage=guard.get('reason','post_move_check')
            return False
        if hover_ms>0:time.sleep(hover_ms/1000.0)
        return True

    def send_drag(self,start_point,end_point,button='left',duration_ms=250):
        import time
        from windows_agent import native
        buttons={'left':(0x0002,0x0004),'right':(0x0008,0x0010),'middle':(0x0020,0x0040)}
        if button not in buttons:return False
        down_flag,up_flag=buttons[button]
        left,top,width,height,to_abs,emit=self._setup_mouse_emitter()
        origin,_,_=native.client_geometry(self.hwnd)
        sx,sy=origin[0]+start_point[0],origin[1]+start_point[1]
        ex,ey=origin[0]+end_point[0],origin[1]+end_point[1]
        if width<2 or height<2:return False
        if not (left<=sx<left+width and top<=sy<top+height and left<=ex<left+width and top<=ey<top+height):
            return False
        guard=self._move_and_guard(start_point,emit,to_abs,origin)
        if guard is None:return False
        if not guard['allowed']:
            self.input_stage=guard.get('reason','post_move_check')
            return False
        self.input_stage='mouse_down'
        if not emit(down_flag):return False
        self.mouse_down_sent=True
        try:
            steps=max(2,min(30,duration_ms//15 if duration_ms>0 else 2))
            step_delay=(duration_ms/1000.0)/steps if duration_ms>0 else 0.01
            for i in range(1,steps+1):
                self.input_stage='drag_move'
                cx=round(sx+(ex-sx)*i/steps)
                cy=round(sy+(ey-sy)*i/steps)
                ax,ay=to_abs(cx,cy)
                if not emit(0xC001,ax,ay):return False
                time.sleep(step_delay)
            return True
        finally:
            self.input_stage='mouse_up'
            emit(up_flag)

    def send_scroll(self,dx=0,dy=0,point=None):
        from windows_agent import native
        left,top,width,height,to_abs,emit=self._setup_mouse_emitter()
        origin,w_win,h_win=native.client_geometry(self.hwnd)
        target_pt=point if point is not None else [max(0,w_win//2),max(0,h_win//2)]
        x,y=origin[0]+target_pt[0],origin[1]+target_pt[1]
        if width<2 or height<2 or not (left<=x<left+width and top<=y<top+height):return False
        guard=self._move_and_guard(target_pt,emit,to_abs,origin)
        if guard is None:return False
        if not guard['allowed']:
            self.input_stage=guard.get('reason','post_move_check')
            return False
        self.input_stage='scroll'
        if dy:
            if not emit(0x0800,data=dy&0xffffffff):return False
        if dx:
            if not emit(0x1000,data=dx&0xffffffff):return False
        return True


def install(server,agent):
    """Install internal guest tools; public access is guarded by host proxy."""
    import time
    frames={}
    original_capture=agent.capture

    def tracked_capture(target_id,region=None):
        result=original_capture(target_id,region)
        if region is None and result.get('png'):
            identity=dict(agent.identity(target_id))
            frames[target_id]={'captured_at':time.monotonic(),'size':(result['width'],result['height']),
                               'identity':identity}
        return result
    agent.capture=tracked_capture

    @server.tool(name='VmDiagnostics')
    def vm_diagnostics():
        """Read VM control thread locations and lock status; never includes variable values."""
        return thread_diagnostics(agent)

    @server.tool(name='HitTest')
    def hit_test(target_id:str,point:list[int]):
        """Read window identity at a target client coordinate without moving or clicking."""
        import win32gui,win32process
        from windows_agent import native
        with agent.lock:
            identity=agent.identity(target_id)
            origin,width,height=native.client_geometry(identity['hwnd'])
            if len(point)!=2 or any(type(x) is not int for x in point) or not (0<=point[0]<width and 0<=point[1]<height):
                return {'outcome':'invalid_point'}
            hwnd=win32gui.WindowFromPoint((origin[0]+point[0],origin[1]+point[1]))
            _,pid=win32process.GetWindowThreadProcessId(hwnd) if hwnd else (0,0)
            return {'outcome':'observed','target_hwnd':identity['hwnd'],'hit_hwnd':hwnd,'hit_pid':pid,
                    'hit_class':win32gui.GetClassName(hwnd) if hwnd else '',
                    'hit_title':win32gui.GetWindowText(hwnd) if hwnd else '',
                    'is_target_or_child':bool(hwnd==identity['hwnd'] or (hwnd and win32gui.IsChild(identity['hwnd'],hwnd)))}

    @server.tool(name='Screenshot', annotations={'readOnlyHint':True})
    def screenshot(target_id:str,region:list[int]|None=None):
        """Capture any visible guest window dynamically and register the fresh full frame."""
        import json
        import time
        from fastmcp.utilities.types import Image
        from hyperv_frame import capture_tracked
        from hyperv_game_capture import capture_game
        if region is not None:
            res=agent.capture(target_id,region)
            return res if res.get('ok') is False else [json.dumps({k:v for k,v in res.items() if k!='png'}),Image(data=res['png'],format='png')]
        metadata,png=capture_tracked(agent,frames,target_id,capture_game,time.monotonic)
        return [json.dumps(metadata),Image(data=png,format='png')]

    @server.tool(name='HyperVDesktopScreenshot', annotations={'readOnlyHint':True})
    def hyperv_desktop_screenshot():
        """Capture the full guest virtual screen without disturbing input or focus."""
        import json
        from fastmcp.utilities.types import Image
        from hyperv_game_capture import capture_desktop
        with agent.lock:
            if agent.state.paused:raise ValueError('Guest agent is paused')
            metadata,png=capture_desktop()
        return [json.dumps(metadata),Image(data=png,format='png')]

    @server.tool(name='_VmCursorProbe')
    def vm_cursor_probe():
        """Internal one-shot cursor isolation check. No click is sent."""
        with agent.lock:
            if not agent.state.task_active:return {'outcome':'no_task_context'}
            agent.state.notify('attention','测试虚拟机鼠标隔离')
            try:return probe_cursor(_Win32ProbeBackend(agent))
            except Exception as exc:return {'outcome':'error','error_type':type(exc).__name__,'message':str(exc)[:200]}

    def _run_vm_foreground_tool(target_id,coordinate_space,consume_frame,point_spec,fn,label):
        with agent.lock:
            if not agent.state.task_active:return {'outcome':'no_task_context'}
            frame=frames.pop(target_id,None) if consume_frame else frames.get(target_id)
            if not frame:return {'outcome':'fresh_full_screenshot_required'}
            client_points={}
            try:
                identity=agent.identity(target_id)
                frame_view={'valid':identity==frame['identity'],'age':time.monotonic()-frame['captured_at'],'size':frame['size']}
                for key,raw in point_spec.items():
                    if raw is None:
                        client_points[key]=None
                    elif coordinate_space=='normalized_1000':
                        pt=normalized_to_client(raw,frame['size'])
                        if pt is None:return {'outcome':'invalid_normalized_point'}
                        client_points[key]=pt
                    elif coordinate_space=='pixels':
                        client_points[key]=raw
                    else:
                        return {'outcome':'invalid_coordinate_space'}
                agent.state.notify('attention',f'准备虚拟机前台{label}',detail=agent.title(identity))
                result=fn(_Win32ClickBackend(agent,identity),client_points,frame_view)
                result['coordinate_space']=coordinate_space
                if result.get('outcome')=='delivered':agent.state.notify('click',f'已发送虚拟机{label}',detail=agent.title(identity))
                else:agent.state.block_current(f'虚拟机{label}未执行')
                return result
            except Exception as exc:
                agent.state.block_current(f'虚拟机{label}需要检查')
                return {'outcome':'error','error_type':type(exc).__name__,'message':str(exc)[:200],
                        'coordinate_space':coordinate_space,'client_points':client_points}

    @server.tool(name='_VmForegroundClick')
    def vm_foreground_click(target_id:str,point:list[int],coordinate_space:str='pixels',button:str='left',clicks:int=1,consume_frame:bool=True):
        """Internal guest-only click; host proxy must verify cursor isolation first."""
        if button not in ('left','right','middle') or clicks not in (1,2):
            return {'outcome':'invalid_argument'}
        res=_run_vm_foreground_tool(
            target_id,coordinate_space,consume_frame,{'point':point},
            lambda b,pts,fv:foreground_click(b,pts['point'],fv,button=button,clicks=clicks),
            '点击',
        )
        res.setdefault('requested_point',point)
        if 'client_points' in res:res['client_point']=res.pop('client_points').get('point')
        return res

    @server.tool(name='_VmForegroundMove')
    def vm_foreground_move(target_id:str,point:list[int],coordinate_space:str='pixels',hover_ms:int=0,consume_frame:bool=True):
        """Internal guest-only move/hover; host proxy must verify cursor isolation first."""
        if type(hover_ms) is not int or not 0<=hover_ms<=30000:
            return {'outcome':'invalid_argument'}
        return _run_vm_foreground_tool(
            target_id,coordinate_space,consume_frame,{'point':point},
            lambda b,pts,fv:foreground_move(b,pts['point'],fv,hover_ms=hover_ms),
            '移动',
        )

    @server.tool(name='_VmForegroundDrag')
    def vm_foreground_drag(target_id:str,start_point:list[int],end_point:list[int],coordinate_space:str='pixels',button:str='left',duration_ms:int=250,consume_frame:bool=True):
        """Internal guest-only drag; host proxy must verify cursor isolation first."""
        if button not in ('left','right','middle') or type(duration_ms) is not int or not 0<=duration_ms<=10000:
            return {'outcome':'invalid_argument'}
        return _run_vm_foreground_tool(
            target_id,coordinate_space,consume_frame,{'start_point':start_point,'end_point':end_point},
            lambda b,pts,fv:foreground_drag(b,pts['start_point'],pts['end_point'],fv,button=button,duration_ms=duration_ms),
            '拖拽',
        )

    @server.tool(name='_VmForegroundScroll')
    def vm_foreground_scroll(target_id:str,dx:int=0,dy:int=0,point:list[int]|None=None,coordinate_space:str='pixels',consume_frame:bool=True):
        """Internal guest-only scroll; host proxy must verify cursor isolation first."""
        if type(dx) is not int or type(dy) is not int or (dx==0 and dy==0):
            return {'outcome':'invalid_argument'}
        return _run_vm_foreground_tool(
            target_id,coordinate_space,consume_frame,{'point':point},
            lambda b,pts,fv:foreground_scroll(b,fv,dx=dx,dy=dy,point=pts['point']),
            '滚动',
        )

    @server.tool(name='_VmBackgroundMove')
    def vm_background_move(target_id:str,point:list[int],hover_ms:int=0):
        """Move and optionally hover inside a background window via WM_MOUSEMOVE."""
        import time,win32gui
        from windows_agent import native
        if type(hover_ms) is not int or not 0<=hover_ms<=30000:
            return {'outcome':'invalid_argument'}
        with agent.lock:
            identity=agent.identity(target_id)
            target,pt,_=native.deepest_child(identity['hwnd'],point[0],point[1])
            native.refuse_unknown(target,'click')
            win32gui.PostMessage(target,0x0200,0,native.packed_point(*pt))
        if hover_ms>0:time.sleep(hover_ms/1000.0)
        return {'target_id':target_id,'point':point,'hover_ms':hover_ms,'route':'wm_mousemove'}

    @server.tool(name='_VmBackgroundDrag')
    def vm_background_drag(target_id:str,start_point:list[int],end_point:list[int],button:str='left',duration_ms:int=250):
        """Drag inside a background window via posted mouse messages."""
        import time,win32gui
        from windows_agent import native
        names={'left':(0x201,0x202,1),'right':(0x204,0x205,2),'middle':(0x207,0x208,16)}
        if button not in names or type(duration_ms) is not int or not 0<=duration_ms<=10000:
            return {'outcome':'invalid_argument'}
        down,up,mask=names[button]
        with agent.lock:
            identity=agent.identity(target_id)
            target,spt,_=native.deepest_child(identity['hwnd'],start_point[0],start_point[1])
            native.refuse_unknown(target,'click')
            _,ept,_=native.deepest_child(identity['hwnd'],end_point[0],end_point[1])
            win32gui.PostMessage(target,0x0200,0,native.packed_point(*spt))
            win32gui.PostMessage(target,down,mask,native.packed_point(*spt))
            steps=max(2,min(30,duration_ms//15 if duration_ms>0 else 2))
            delay=(duration_ms/1000.0)/steps if duration_ms>0 else 0.01
            for i in range(1,steps+1):
                cx=round(spt[0]+(ept[0]-spt[0])*i/steps)
                cy=round(spt[1]+(ept[1]-spt[1])*i/steps)
                win32gui.PostMessage(target,0x0200,mask,native.packed_point(cx,cy))
                time.sleep(delay)
            win32gui.PostMessage(target,up,0,native.packed_point(*ept))
        return {'target_id':target_id,'start_point':start_point,'end_point':end_point,'button':button,'route':'wm_drag'}

    @server.tool(name='_VmBackgroundScroll')
    def vm_background_scroll(target_id:str,dx:int=0,dy:int=0,point:list[int]|None=None):
        """Scroll vertically and/or horizontally at optional point in a background window."""
        import win32gui
        from windows_agent import native
        with agent.lock:
            identity=agent.identity(target_id)
            _,w_win,h_win=native.client_geometry(identity['hwnd'])
            pt=point if point is not None else [max(0,w_win//2),max(0,h_win//2)]
            target,_,screen=native.deepest_child(identity['hwnd'],pt[0],pt[1])
            native.refuse_unknown(target,'scroll')
            lp=native.packed_point(*screen)
            if dy:win32gui.PostMessage(target,0x020A,(dy&0xffff)<<16,lp)
            if dx:win32gui.PostMessage(target,0x020E,(dx&0xffff)<<16,lp)
        return {'target_id':target_id,'dx':dx,'dy':dy,'point':pt,'route':'wm_scroll'}

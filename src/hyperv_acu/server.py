"""ChatGPT MCP bridge for the configured Hyper-V guest only."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from hyperv_acu.bootstrap import ensure_paths, load_settings
ROOT = ensure_paths()
_SETTINGS = load_settings(ROOT)

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from mcp.types import TextContent

from hyperv_acu.app_policy import VM_ID, require_vm, require_capture, pixel_point, normalized_point
from hyperv_acu import input_guard

PYTHON = _SETTINGS.get('python_exe', sys.executable)
PROXY = str(Path(__file__).resolve().with_name('proxy.py'))
server=FastMCP('Hyper-V',version='0.1.0',instructions=(
    'This connection operates only the configured Hyper-V guest. '
    'Use its exact VM ID, check agent state, respect user takeover and pause. '
    'Inspect actual fresh screenshots before and after each action. '
    'Never operate the host desktop; stop on errors or refused input.'))
_lock=asyncio.Lock()
_captures={}
INPUT_PATH=ROOT/'runtime'/'hyperv-input-block.json'
INVENTORY=Path(r'C:\ProgramData\HyperVApp\inventory.json')

_clients={}


def connection(timeout:float=45,vm_id:str|None=None):
    env=None
    if vm_id:
        from hyperv_acu import vm_pool
        try:
            vm=vm_pool.get_managed_vm(vm_id)
            env=dict(os.environ)
            env['HYPERV_VM_ID']=vm['id']
            if vm.get('mac'):env['HYPERV_VM_MAC']=vm['mac']
            if vm.get('token_path'):env['HYPERV_GUEST_TOKEN_PATH']=vm['token_path']
        except ValueError:
            pass
    return Client(StdioTransport(PYTHON,[PROXY],env=env),mode='2026-07-28',timeout=timeout)


@asynccontextmanager
async def pooled_connection(timeout:float=45,vm_id:str|None=None):
    key=((vm_id or VM_ID or '').strip().lower(),float(timeout))
    client=_clients.get(key)
    if client is None or not client.is_connected():
        if client is not None:
            _clients.pop(key,None)
            try:await client.__aexit__(None,None,None)
            except Exception:pass
        client=connection(timeout=timeout,vm_id=vm_id)
        await client.__aenter__()
        _clients[key]=client
    try:
        yield client
    except Exception as exc:
        msg=str(exc).lower()
        if not client.is_connected() or any(k in msg for k in ('closed','broken pipe','eof','connection','transport')):
            _clients.pop(key,None)
            try:await client.__aexit__(None,None,None)
            except Exception:pass
        raise


def _invoke_broker(operation:str,payload:dict|None=None)->dict:
    helper=ROOT/'scripts'/'admin-request.ps1'
    cmd=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(helper),'-Operation',operation]
    if payload is not None:
        cmd.extend(['-PayloadJson',json.dumps(payload)])
    proc=subprocess.run(cmd,capture_output=True,text=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if proc.returncode!=0:
        raise RuntimeError((proc.stderr or proc.stdout or f'Broker operation {operation} failed').strip())
    data=json.loads(proc.stdout.lstrip('\ufeff'))
    if not isinstance(data,dict) or data.get('ok') is not True:
        raise RuntimeError(str(data.get('error') if isinstance(data,dict) else f'Broker operation {operation} failed'))
    return data

_PIP_MODULE=None
_THUMBNAILS:dict[str,subprocess.Popen]={}


def _ensure_vm_thumbnail(vm_id:str,vm_name:str=''):
    if os.environ.get('HYPERV_DISABLE_PIP')=='1' or not vm_id:return
    key=vm_id.strip().lower()
    existing=_THUMBNAILS.get(key)
    if existing is not None and existing.poll() is None:return
    title=vm_name
    if not title:
        from hyperv_acu import vm_pool
        try:
            rec=vm_pool.get_managed_vm(key)
        except ValueError:
            rec=None
        title=(rec or {}).get('name') or str(load_settings().get('vm_name','')) or key[:8]
    try:
        env=dict(os.environ)
        env['PYTHONPATH']=str(ROOT/'src')
        env['HYPERV_VM_ID']=key
        _THUMBNAILS[key]=subprocess.Popen(
            [PYTHON,'-m','hyperv_acu.pip_thumbnail','--title',title,'--notch'],
            env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
        )
    except Exception:
        pass


def notify_notch_thumbnail():
    global _PIP_MODULE
    if os.environ.get('HYPERV_DISABLE_PIP')=='1':return
    try:
        if _PIP_MODULE is None:
            from hyperv_acu import pip_thumbnail
            _PIP_MODULE=pip_thumbnail
        _PIP_MODULE.notify_show()
    except Exception:
        pass


_STATUS_CACHE={'time':0.0,'state':None}
_STATUS_TTL=5.0


async def status_checked(client):
    now=time.monotonic()
    cached=_STATUS_CACHE['state']
    if cached is not None and now-_STATUS_CACHE['time']<=_STATUS_TTL:
        return cached
    result=await client.call_tool('AgentStatus',{})
    state=result.data
    if not isinstance(state,dict) or state.get('paused'):
        _STATUS_CACHE['state']=None
        raise RuntimeError('Guest agent status is unavailable or paused')
    _STATUS_CACHE['time']=now
    _STATUS_CACHE['state']=state
    notify_notch_thumbnail()
    return state


async def ensure_task_active(client,task:str='Hyper-V automation',step:str='Running'):
    state=await status_checked(client)
    is_task_active=bool(state.get('task_active'))
    if not is_task_active:
        await client.call_tool('TaskContext',{'task':task,'step':step,'completed':False})
        _STATUS_CACHE['state']=None
    elif task!='Hyper-V automation' and state.get('task')!=task:
        raise RuntimeError('Another guest task is active')
    return state


def _norm_target(target_id:str)->str:
    return 'desktop' if not target_id or target_id=='desktop' else target_id


async def _capture_target(client,vm_id:str,target_id:str='',should_auto_confirm:bool=True):
    _captures.pop(vm_id,None)
    norm=_norm_target(target_id)
    if norm=='desktop':
        result=await client.call_tool('HyperVDesktopScreenshot',{})
    else:
        windows=(await client.call_tool('WindowList',{'title':''})).data
        if not isinstance(windows,list):raise RuntimeError('Guest window list is unavailable')
        matches=[w for w in windows if isinstance(w,dict) and w.get('target_id')==norm]
        if len(matches)!=1:raise ValueError('Target is missing or stale')
        result=await client.call_tool('Screenshot',{'target_id':norm})
    width,height=metadata_and_image(result)
    capture={'target':norm,'time':time.monotonic(),'width':width,'height':height,'tracked':True}
    _captures[vm_id]=capture
    review_token=input_guard.observed(INPUT_PATH,norm)
    if review_token and should_auto_confirm:
        input_guard.confirm(INPUT_PATH,norm,review_token,'Observed post-action screenshot')
    return result,capture,review_token

def metadata_and_image(result):
    data=None; has_image=False
    for item in result.content:
        if getattr(item,'type',None)=='image':has_image=True
        if getattr(item,'type',None)=='text':
            try:
                value=json.loads(item.text)
                if isinstance(value,dict) and 'width' in value and 'height' in value:data=value
            except (TypeError,ValueError):pass
    if not has_image or not data or data.get('ok') is False:
        raise RuntimeError('Screenshot did not return a verifiable image and dimensions')
    width,height=data['width'],data['height']
    if type(width) is not int or type(height) is not int or width<=0 or height<=0:
        raise RuntimeError('Screenshot dimensions are invalid')
    return width,height

def read_vm_inventory():
    helper=ROOT/'scripts'/'admin-request.ps1'
    try:
        broker=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(helper),'-Operation','list_vms'],capture_output=True,text=True,timeout=12,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if broker.returncode==0:
            data=json.loads(broker.stdout.lstrip('\ufeff'))
            if data.get('ok') is True and isinstance(data.get('vms'),list):return data['vms']
    except (OSError,subprocess.TimeoutExpired,ValueError,TypeError):pass
    started=time.time()
    result=subprocess.run(['schtasks.exe','/Run','/TN','HyperVApp-ReadInventory'],capture_output=True,text=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('Hyper-V inventory service could not be started')
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            data=json.loads(INVENTORY.read_text(encoding='utf-8-sig'))
            generated=datetime.fromisoformat(data['generated_at']).timestamp()
            if generated>=started-1:
                if data.get('error'):raise RuntimeError('Hyper-V inventory query failed: '+str(data['error']))
                if not isinstance(data.get('vms'),list):raise RuntimeError('Hyper-V inventory response is invalid')
                return data['vms']
        except (OSError,ValueError,KeyError,TypeError):pass
        time.sleep(.25)
    raise RuntimeError('Hyper-V inventory query timed out')

def format_vm_inventory(vms,connected,managed_ids=None):
    from hyperv_acu import vm_pool
    pool_vms=vm_pool.load_pool().get('vms',{})
    managed=set(managed_ids) if managed_ids is not None else (set(pool_vms.keys())|({VM_ID} if VM_ID else set()))
    output=[]
    for vm in vms:
        if not isinstance(vm,dict) or not all(isinstance(vm.get(k),str) for k in ('name','id','state')):
            raise RuntimeError('Hyper-V inventory contains an invalid VM')
        vm_key=vm['id'].lower()
        configured=vm_key in managed
        is_connected=connected.get(vm_key,False) if isinstance(connected,dict) else (connected if configured else None)
        entry={**vm,'control_configured':configured,'guest_agent_connected':is_connected if configured else None}
        if vm_key in pool_vms:
            entry['mode']=pool_vms[vm_key].get('mode','ephemeral')
        output.append(entry)
    return output

@server.tool(name='list_vms',annotations={'readOnlyHint':True})
async def list_vms():
    """List all Hyper-V VMs and identify managed sandboxes with desktop control configured."""
    from hyperv_acu import vm_pool
    vms=await asyncio.to_thread(read_vm_inventory)
    managed=vm_pool.list_managed_ids()|({VM_ID} if VM_ID else set())
    connected={}
    for vm in vms:
        if isinstance(vm,dict) and vm.get('id','').lower() in managed and vm.get('state')=='Running':
            vid=vm['id'].lower()
            try:
                async with pooled_connection(vm_id=vid) as client:
                    result=await client.call_tool('AgentStatus',{})
                    is_up=isinstance(result.data,dict)
                    connected[vid]=is_up
                    if is_up:_ensure_vm_thumbnail(vid,vm.get('name',''))
            except Exception:
                connected[vid]=False
    return format_vm_inventory(vms,connected,managed_ids=managed)

@server.tool(name='agent_status',annotations={'readOnlyHint':True})
async def agent_status(vm_id:str):
    require_vm(vm_id)
    async with _lock,pooled_connection(vm_id=vm_id) as client:
        res=await client.call_tool('AgentStatus',{})
        if isinstance(res.data,dict):_ensure_vm_thumbnail(vm_id)
        return res.content

@server.tool(name='list_windows',annotations={'readOnlyHint':True})
async def list_windows(vm_id:str,title:str=''):
    require_vm(vm_id)
    async with _lock,pooled_connection(vm_id=vm_id) as client:
        return (await client.call_tool('WindowList',{'title':title})).content

@server.tool(name='screenshot',annotations={'readOnlyHint':True})
async def screenshot(vm_id:str,target_id:str=''):
    """Capture the full guest desktop (default) or a specific target_id window."""
    require_vm(vm_id)
    async with _lock,pooled_connection() as client:
        await status_checked(client)
        result,_,_=await _capture_target(client,vm_id,target_id,should_auto_confirm=True)
        return result.content

@server.tool(name='window_snapshot',annotations={'readOnlyHint':True})
async def window_snapshot(vm_id:str,target_id:str,include_screenshot:bool=True):
    require_vm(vm_id)
    async with _lock,pooled_connection() as client:
        return (await client.call_tool('WindowSnapshot',{'target_id':target_id,'include_screenshot':include_screenshot})).content

def _resolve_point(x:int,y:int,coordinate_space:str,capture:dict)->list[int]:
    if coordinate_space not in ('pixels','normalized_1000'):
        raise ValueError('Use pixels or normalized_1000')
    pt=(normalized_point(x,y,capture['width'],capture['height']) if coordinate_space=='normalized_1000'
        else pixel_point(x,y,capture['width'],capture['height']))
    return list(pt)


def _validate_button(button:str):
    if button not in ('left','right','middle'):
        raise ValueError('button must be left, right, or middle')


def _validate_clicks(clicks:int):
    if type(clicks) is not int or clicks not in (1,2):
        raise ValueError('clicks must be 1 or 2')


def _resolve_scroll_deltas(delta:int,direction:str,dx:int,dy:int)->tuple[int,int]:
    if direction not in ('vertical','horizontal'):
        raise ValueError('direction must be vertical or horizontal')
    if type(delta) is not int or type(dx) is not int or type(dy) is not int:
        raise ValueError('Scroll deltas must be integers')
    if dx==0 and dy==0:
        if delta==0:raise ValueError('Provide a non-zero delta, dx, or dy')
        if direction=='horizontal':dx=delta
        else:dy=delta
    return dx,dy


async def _dispatch_click(client,target_id:str,capture:dict,x:int,y:int,coordinate_space:str,button:str,clicks:int,should_consume_frame:bool=True,should_guard_input:bool=True):
    _validate_button(button)
    _validate_clicks(clicks)
    point=_resolve_point(x,y,coordinate_space,capture)
    return (await client.call_tool('click',{
        'target_id':_norm_target(target_id),'point':point,'button':button,'clicks':clicks,
        'consume_frame':should_consume_frame,'guard_input':should_guard_input,
    })).content


async def _dispatch_move(client,target_id:str,capture:dict,x:int,y:int,coordinate_space:str,hover_ms:int,should_consume_frame:bool=True,should_guard_input:bool=True):
    if type(hover_ms) is not int or not 0<=hover_ms<=30000:
        raise ValueError('hover_ms must be an integer between 0 and 30000')
    point=_resolve_point(x,y,coordinate_space,capture)
    return (await client.call_tool('move',{
        'target_id':_norm_target(target_id),'point':point,'hover_ms':hover_ms,
        'consume_frame':should_consume_frame,'guard_input':should_guard_input,
    })).content


async def _dispatch_drag(client,target_id:str,capture:dict,start_x:int,start_y:int,end_x:int,end_y:int,coordinate_space:str,button:str,duration_ms:int,should_consume_frame:bool=True,should_guard_input:bool=True):
    _validate_button(button)
    if type(duration_ms) is not int or not 0<=duration_ms<=10000:
        raise ValueError('duration_ms must be an integer between 0 and 10000')
    start_point=_resolve_point(start_x,start_y,coordinate_space,capture)
    end_point=_resolve_point(end_x,end_y,coordinate_space,capture)
    return (await client.call_tool('drag',{
        'target_id':_norm_target(target_id),'start_point':start_point,'end_point':end_point,
        'button':button,'duration_ms':duration_ms,'consume_frame':should_consume_frame,'guard_input':should_guard_input,
    })).content


async def _dispatch_scroll(client,target_id:str,capture:dict|None,delta:int,point:list[int]|None,x:int|None,y:int|None,coordinate_space:str,direction:str,dx:int,dy:int,should_consume_frame:bool=True,should_guard_input:bool=True):
    dx,dy=_resolve_scroll_deltas(delta,direction,dx,dy)
    resolved_point=point
    if x is not None or y is not None:
        if x is None or y is None:raise ValueError('Both x and y are required when scrolling at a position')
        if not capture:raise ValueError('A fresh screenshot of this exact target is required')
        resolved_point=_resolve_point(x,y,coordinate_space,capture)
    return (await client.call_tool('scroll',{
        'target_id':_norm_target(target_id),'dx':dx,'dy':dy,'point':resolved_point,
        'consume_frame':should_consume_frame,'guard_input':should_guard_input,
    })).content


@server.tool(name='click')
async def click(vm_id:str,x:int,y:int,target_id:str='',coordinate_space:str='pixels',button:str='left',clicks:int=1):
    """Click screenshot coordinates on full desktop (default) or target_id window inside the VM."""
    require_vm(vm_id)
    norm=_norm_target(target_id)
    async with _lock,pooled_connection() as client:
        input_guard.require_clear(INPUT_PATH)
        await ensure_task_active(client)
        capture=_captures.pop(vm_id,None)
        require_capture(capture,norm,time.monotonic())
        return await _dispatch_click(client,norm,capture,x,y,coordinate_space,button,clicks)


@server.tool(name='move')
async def move(vm_id:str,x:int,y:int,target_id:str='',coordinate_space:str='pixels',hover_ms:int=0):
    """Move the guest cursor to screenshot coordinates on full desktop (default) or target_id window."""
    require_vm(vm_id)
    norm=_norm_target(target_id)
    async with _lock,pooled_connection() as client:
        input_guard.require_clear(INPUT_PATH)
        await ensure_task_active(client)
        capture=_captures.pop(vm_id,None)
        require_capture(capture,norm,time.monotonic())
        return await _dispatch_move(client,norm,capture,x,y,coordinate_space,hover_ms)


@server.tool(name='drag')
async def drag(vm_id:str,start_x:int,start_y:int,end_x:int,end_y:int,target_id:str='',coordinate_space:str='pixels',button:str='left',duration_ms:int=250):
    """Drag the guest cursor from start to end coordinates on full desktop (default) or target_id window."""
    require_vm(vm_id)
    norm=_norm_target(target_id)
    async with _lock,pooled_connection() as client:
        input_guard.require_clear(INPUT_PATH)
        await ensure_task_active(client)
        capture=_captures.pop(vm_id,None)
        require_capture(capture,norm,time.monotonic())
        return await _dispatch_drag(client,norm,capture,start_x,start_y,end_x,end_y,coordinate_space,button,duration_ms)


@server.tool(name='focus_window')
async def focus_window(vm_id:str,target_id:str):
    """Focus a guest window after checking VM user activity; never affects the host desktop."""
    require_vm(vm_id)
    async with _lock,pooled_connection() as client:
        input_guard.require_clear(INPUT_PATH)
        await ensure_task_active(client)
        _captures.pop(vm_id,None)
        return (await client.call_tool('focus_window',{'target_id':target_id})).content


@server.tool(name='key')
async def key(vm_id:str,key:str,target_id:str='',point:list[int]|None=None,element_token:str|None=None):
    """Send a key or key chord desktop-wide (default) or to target_id window."""
    require_vm(vm_id)
    norm=_norm_target(target_id)
    async with _lock,pooled_connection() as client:
        input_guard.require_clear(INPUT_PATH)
        await ensure_task_active(client)
        require_capture(_captures.pop(vm_id,None),norm,time.monotonic())
        return (await client.call_tool('Key',{'target_id':norm,'key':key,'point':point,'element_token':element_token})).content


@server.tool(name='type')
async def type_text(vm_id:str,text:str,target_id:str='',element_token:str|None=None,point:list[int]|None=None,clear:bool=True):
    """Type Unicode text desktop-wide (default) or into target_id window."""
    require_vm(vm_id)
    norm=_norm_target(target_id)
    async with _lock,pooled_connection() as client:
        input_guard.require_clear(INPUT_PATH)
        await ensure_task_active(client)
        require_capture(_captures.pop(vm_id,None),norm,time.monotonic())
        return (await client.call_tool('Type',{'target_id':norm,'text':text,'element_token':element_token,'point':point,'clear':clear})).content


@server.tool(name='scroll')
async def scroll(vm_id:str,delta:int=0,target_id:str='',element_token:str|None=None,point:list[int]|None=None,x:int|None=None,y:int|None=None,coordinate_space:str='pixels',direction:str='vertical',dx:int=0,dy:int=0):
    """Scroll vertically or horizontally on full desktop (default) or target_id window."""
    require_vm(vm_id)
    norm=_norm_target(target_id)
    async with _lock,pooled_connection() as client:
        input_guard.require_clear(INPUT_PATH)
        await ensure_task_active(client)
        capture=_captures.pop(vm_id,None)
        require_capture(capture,norm,time.monotonic())
        return await _dispatch_scroll(client,norm,capture,delta,point,x,y,coordinate_space,direction,dx,dy)


@server.tool(name='wait')
async def wait(seconds:float,vm_id:str|None=None):
    """Wait for the specified duration in seconds (0 < seconds <= 60)."""
    if vm_id is not None:require_vm(vm_id)
    if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not 0<seconds<=60:
        raise ValueError('seconds must be a number between 0 and 60')
    await asyncio.sleep(float(seconds))
    return {'waited_seconds':float(seconds)}


def _extract_blocked_reason(action_result)->str|None:
    if not isinstance(action_result,list):return None
    for part in action_result:
        raw_text=getattr(part,'text','')
        if not isinstance(raw_text,str) or not raw_text.startswith('{'):continue
        try:
            payload=json.loads(raw_text)
        except (ValueError,TypeError):
            continue
        outcome=payload.get('outcome')
        if outcome and outcome!='delivered':
            return payload.get('reason') or outcome
    return None


async def _execute_batch_step(client,vm_id:str,target_id:str,capture:dict,item:dict,task_name:str,default_step:str,should_consume_frame:bool):
    action_name=item['action']
    if action_name=='click':
        res=await _dispatch_click(client,target_id,capture,item['x'],item['y'],
                                  item.get('coordinate_space','pixels'),item.get('button','left'),
                                  item.get('clicks',1),should_consume_frame=should_consume_frame,should_guard_input=False)
        return res,capture
    if action_name=='double_click':
        first_click=await _dispatch_click(client,target_id,capture,item['x'],item['y'],
                                          item.get('coordinate_space','pixels'),item.get('button','left'),
                                          1,should_consume_frame=False,should_guard_input=False)
        await wait(item.get('seconds',0.05))
        second_click=await _dispatch_click(client,target_id,capture,item['x'],item['y'],
                                           item.get('coordinate_space','pixels'),item.get('button','left'),
                                           1,should_consume_frame=should_consume_frame,should_guard_input=False)
        return [*(first_click if isinstance(first_click,list) else []),*(second_click if isinstance(second_click,list) else [])],capture
    if action_name=='move':
        res=await _dispatch_move(client,target_id,capture,item['x'],item['y'],
                                 item.get('coordinate_space','pixels'),item.get('hover_ms',0),
                                 should_consume_frame=should_consume_frame,should_guard_input=False)
        return res,capture
    if action_name=='drag':
        res=await _dispatch_drag(client,target_id,capture,item['start_x'],item['start_y'],
                                 item['end_x'],item['end_y'],item.get('coordinate_space','pixels'),
                                 item.get('button','left'),item.get('duration_ms',250),
                                 should_consume_frame=should_consume_frame,should_guard_input=False)
        return res,capture
    if action_name=='scroll':
        res=await _dispatch_scroll(client,target_id,capture,item.get('delta',0),
                                   item.get('point'),item.get('x'),item.get('y'),
                                   item.get('coordinate_space','pixels'),item.get('direction','vertical'),
                                   item.get('dx',0),item.get('dy',0),should_consume_frame=should_consume_frame,should_guard_input=False)
        return res,capture
    if action_name=='key':
        res=(await client.call_tool('Key',{'target_id':target_id,'key':item['key'],'point':item.get('point'),'element_token':item.get('element_token')})).content
        return res,capture
    if action_name=='type':
        res=(await client.call_tool('Type',{'target_id':target_id,'text':item['text'],'element_token':item.get('element_token'),'point':item.get('point'),'clear':item.get('clear',True)})).content
        return res,capture
    if action_name=='wait':
        wait_seconds=item['seconds']
        if isinstance(wait_seconds,bool) or not isinstance(wait_seconds,(int,float)) or not 0<wait_seconds<=60:
            raise ValueError('seconds must be a number between 0 and 60')
        await asyncio.sleep(float(wait_seconds))
        return {'waited_seconds':float(wait_seconds)},capture
    if action_name=='screenshot':
        _,new_capture,_=await _capture_target(client,vm_id,target_id,should_auto_confirm=True)
        return {'width':new_capture['width'],'height':new_capture['height'],'tracked':new_capture['tracked']},new_capture
    if action_name=='task_context':
        res=(await client.call_tool('TaskContext',{
            'task':item.get('task',task_name),'step':item.get('step',default_step),
            'completed':bool(item.get('completed',False)),
        })).content
        return res,capture
    raise ValueError(f'Unsupported batch action: {action_name}')


@server.tool(name='batch')
async def batch(vm_id:str,actions:list[dict],target_id:str='',stop_on_error:bool=True,task:str|None=None,step:str|None=None,should_complete_task:bool=False,observed_result:str|None=None):
    """Execute a sequence of guest actions (click, double_click, move, drag, scroll, key, type, wait, screenshot, task_context) desktop-wide (default) or scoped to target_id."""
    require_vm(vm_id)
    norm=_norm_target(target_id)
    if not isinstance(actions,list) or not 1<=len(actions)<=50:
        raise ValueError('actions must be a list of 1 to 50 action dicts')
    for item in actions:
        if not isinstance(item,dict) or not isinstance(item.get('action'),str):
            raise ValueError('Each batch entry must be a dict with an action string')
    async with _lock,pooled_connection() as client:
        if observed_result is not None:
            review_token=input_guard.observed(INPUT_PATH,norm)
            if review_token:input_guard.confirm(INPUT_PATH,norm,review_token,observed_result)
        input_guard.require_clear(INPUT_PATH)
        task_name=task or 'Hyper-V automation'
        await ensure_task_active(client,task=task_name,step=step or 'Batch running')
        capture=_captures.pop(vm_id,None)
        require_capture(capture,norm,time.monotonic())
        pointer_actions={'click','double_click','move','drag','scroll'}
        pointer_indices=[index for index,entry in enumerate(actions) if entry.get('action') in pointer_actions]
        last_pointer_index=pointer_indices[-1] if pointer_indices else -1
        if pointer_indices:
            input_guard.start(INPUT_PATH,target_id)
        results=[];last_outcome={'outcome':'delivered'}
        try:
            for action_index,item in enumerate(actions):
                action_name=item['action']
                try:
                    action_result,capture=await _execute_batch_step(
                        client,vm_id,target_id,capture,item,task_name,step or 'Running',action_index==last_pointer_index,
                    )
                    parsed_result=action_result if not isinstance(action_result,list) else [getattr(part,'text',str(part)) for part in action_result]
                    blocked_reason=_extract_blocked_reason(action_result)
                    if blocked_reason:
                        last_outcome={'outcome':blocked_reason}
                        results.append({'index':action_index,'action':action_name,'ok':False,'reason':blocked_reason,'result':parsed_result})
                        if stop_on_error:break
                    else:
                        results.append({'index':action_index,'action':action_name,'ok':True,'result':parsed_result})
                except TimeoutError as exc:
                    last_outcome={'outcome':'timeout'}
                    results.append({'index':action_index,'action':action_name,'ok':False,'reason':'timeout','error':str(exc) or 'timed out'})
                    if stop_on_error:break
                except Exception as exc:
                    last_outcome={'outcome':'error'}
                    results.append({'index':action_index,'action':action_name,'ok':False,'reason':'error','error':str(exc)})
                    if stop_on_error:break
            if pointer_indices:
                if last_outcome.get('outcome')=='delivered':
                    if INPUT_PATH.exists():INPUT_PATH.unlink(missing_ok=True)
                else:input_guard.finish(INPUT_PATH,last_outcome)
            if should_complete_task:
                await client.call_tool('TaskContext',{'task':task_name,'step':'Completed','completed':True})
        except BaseException:
            if pointer_indices:input_guard.finish(INPUT_PATH,{'outcome':'input_outcome_unknown'})
            raise
        is_all_succeeded=all(entry['ok'] for entry in results) and len(results)==len(actions)
        return {'ok':is_all_succeeded,'completed':sum(1 for entry in results if entry['ok']),'total':len(actions),'results':results}


@server.tool(name='exec_powershell')
async def exec_powershell(vm_id:str,command:str,timeout_seconds:int=30,working_directory:str|None=None):
    """Execute a PowerShell command inside the Hyper-V guest and await its output."""
    require_vm(vm_id)
    if not isinstance(command,str) or not command.strip():
        raise ValueError('command must be a non-empty string')
    if type(timeout_seconds) is not int or not 1<=timeout_seconds<=300:
        raise ValueError('timeout_seconds must be an integer between 1 and 300')
    async with _lock,pooled_connection(timeout=max(45.0,float(timeout_seconds)+15.0)) as client:
        await status_checked(client)
        return (await client.call_tool('ExecPowerShell',{
            'command':command,'timeout_seconds':timeout_seconds,'working_directory':working_directory,
        })).content

@server.tool(name='create_vm')
async def create_vm(name:str,mode:str='ephemeral',base_vhdx:str='',memory_gb:int=4,cpu_count:int=2):
    """Clone a new managed Hyper-V sandbox VM from the base differencing image."""
    from hyperv_acu import vm_pool
    if mode not in vm_pool.VALID_MODES:
        raise ValueError(f'Unsupported VM mode: {mode}')
    settings=load_settings()
    base=base_vhdx or str(settings.get('base_vhdx',''))
    if not base:
        raise ValueError('Configure base_vhdx in config/settings.local.json or pass base_vhdx')
    limit=int(settings.get('max_managed_vms',4))
    if len(vm_pool.load_pool()['vms'])>=limit:
        raise ValueError(f'Managed VM limit reached ({limit})')
    reply=await asyncio.to_thread(_invoke_broker,'create_vm',{
        'name':name,'mode':mode,'base_vhdx':base,'memory_gb':memory_gb,'cpu_count':cpu_count,
    })
    vm=reply['vm']
    record=vm_pool.register_vm(vm)
    if os.environ.get('HYPERV_DISABLE_PIP')!='1':
        try:
            subprocess.Popen(['vmconnect.exe','localhost',record['name']],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except Exception:
            pass
        _ensure_vm_thumbnail(record['id'],record['name'])
    return record

@server.tool(name='destroy_vm')
async def destroy_vm(vm_id:str):
    """Stop, remove, and delete disk for a managed Hyper-V sandbox VM."""
    from hyperv_acu import vm_pool
    pool=vm_pool.load_pool()['vms']
    key=vm_id.strip().lower()
    if key not in pool:
        raise ValueError('Only dynamically created managed VMs in runtime/vms.json can be destroyed')
    reply=await asyncio.to_thread(_invoke_broker,'destroy_vm',{'vm_id':key,'diff_vhdx':pool[key].get('diff_vhdx','')})
    vm_pool.unregister_vm(key)
    return reply

@server.tool(name='start_vm')
async def start_vm(vm_id:str):
    """Start a managed Hyper-V VM."""
    require_vm(vm_id)
    reply=await asyncio.to_thread(_invoke_broker,'start_vm',{'vm_id':vm_id.strip().lower()})
    _ensure_vm_thumbnail(vm_id)
    return reply

@server.tool(name='stop_vm')
async def stop_vm(vm_id:str,force:bool=False):
    """Stop a managed Hyper-V VM."""
    require_vm(vm_id)
    return await asyncio.to_thread(_invoke_broker,'stop_vm',{'vm_id':vm_id.strip().lower(),'force':force})

@server.tool(name='checkpoint_vm')
async def checkpoint_vm(vm_id:str,name:str=''):
    """Create a Hyper-V checkpoint for a managed VM."""
    require_vm(vm_id)
    return await asyncio.to_thread(_invoke_broker,'checkpoint_vm',{'vm_id':vm_id.strip().lower(),'name':name})

@server.tool(name='restore_vm')
async def restore_vm(vm_id:str,checkpoint_name:str):
    """Restore a managed Hyper-V VM to a named checkpoint."""
    require_vm(vm_id)
    return await asyncio.to_thread(_invoke_broker,'restore_vm',{'vm_id':vm_id.strip().lower(),'checkpoint_name':checkpoint_name})

def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--http',action='store_true')
    parser.add_argument('--port',type=int,default=18800)
    args=parser.parse_args()
    if args.http:
        server.run(transport='streamable-http',host='127.0.0.1',port=args.port)
    else:
        server.run(transport='stdio')

if __name__=='__main__':main()

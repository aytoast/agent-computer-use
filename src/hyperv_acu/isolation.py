"""Guest cursor isolation checks; never sends input to the host."""
import threading
from hyperv_acu import host_guard as guard
from hyperv_acu import input_guard

async def monitored_call(client,tool,args,read_state):
    initial=read_state();samples=[initial];stop=threading.Event()
    def sample():
        while not stop.wait(.02):
            try:samples.append(read_state())
            except Exception:samples.append(None)
    thread=threading.Thread(target=sample,daemon=True);thread.start()
    try:result=await client.call_tool(tool,args)
    finally:
        stop.set();thread.join(timeout=2)
        try:samples.append(read_state())
        except Exception:samples.append(None)
    return result.data,samples

def _validate_point(point):
    if not isinstance(point,list) or len(point)!=2 or any(type(v) is not int or v<0 for v in point):
        raise ValueError('Nonnegative screenshot pixel coordinates are required')


async def _preflight_ready(client, read_state):
    host = read_state()
    if not guard.vm_ready(host):
        return 'vm_foreground'
    status = (await client.call_tool('AgentStatus', {})).data
    if not isinstance(status, dict) or status.get('paused') or not status.get('task_active'):
        return 'guest_not_ready'
    host = read_state()
    if not guard.vm_ready(host):
        return 'vm_foreground'
    return None


async def _run_guarded_foreground(client,target,tool,args,input_path,*,guard_input=True,read_state=guard.read_host_state):
    if guard_input:input_guard.require_clear(input_path)
    if not isinstance(target,str) or not target:
        raise ValueError('An exact target is required')
    blocked = await _preflight_ready(client, read_state)
    if blocked:
        return {'outcome': blocked}
    if guard_input:input_guard.start(input_path,target)
    try:
        result,samples=await monitored_call(client,tool,args,read_state)
        if not isinstance(result,dict) or not guard.vm_uninterrupted(samples):
            result={'outcome':'input_outcome_unknown','reason':'vm_takeover_or_result_invalid'}
        if guard_input:input_guard.finish(input_path,result)
        return result
    except BaseException:
        if guard_input:input_guard.finish(input_path,{'outcome':'input_outcome_unknown'})
        raise


async def click(client,target,point,input_path,*,button='left',clicks=1,consume_frame=True,guard_input=True,read_state=guard.read_host_state):
    _validate_point(point)
    return await _run_guarded_foreground(
        client,target,'_VmForegroundClick',
        {'target_id':target,'point':point,'coordinate_space':'pixels','button':button,'clicks':clicks,'consume_frame':consume_frame},
        input_path,guard_input=guard_input,read_state=read_state,
    )


async def move(client,target,point,input_path,*,hover_ms=0,consume_frame=True,guard_input=True,read_state=guard.read_host_state):
    _validate_point(point)
    return await _run_guarded_foreground(
        client,target,'_VmForegroundMove',
        {'target_id':target,'point':point,'coordinate_space':'pixels','hover_ms':hover_ms,'consume_frame':consume_frame},
        input_path,guard_input=guard_input,read_state=read_state,
    )


async def drag(client,target,start_point,end_point,input_path,*,button='left',duration_ms=250,consume_frame=True,guard_input=True,read_state=guard.read_host_state):
    _validate_point(start_point)
    _validate_point(end_point)
    return await _run_guarded_foreground(
        client,target,'_VmForegroundDrag',
        {'target_id':target,'start_point':start_point,'end_point':end_point,'coordinate_space':'pixels',
         'button':button,'duration_ms':duration_ms,'consume_frame':consume_frame},
        input_path,guard_input=guard_input,read_state=read_state,
    )


async def scroll(client,target,input_path,*,dx=0,dy=0,point=None,consume_frame=True,guard_input=True,read_state=guard.read_host_state):
    if point is not None:_validate_point(point)
    return await _run_guarded_foreground(
        client,target,'_VmForegroundScroll',
        {'target_id':target,'dx':dx,'dy':dy,'point':point,'coordinate_space':'pixels','consume_frame':consume_frame},
        input_path,guard_input=guard_input,read_state=read_state,
    )

async def focus(client,target,*,read_state=guard.read_host_state):
    if not isinstance(target,str) or not target:raise ValueError('An exact target is required')
    blocked = await _preflight_ready(client, read_state)
    if blocked:
        return {'outcome': blocked}
    result,samples=await monitored_call(client,'_VmFocusWindow',{'target_id':target},read_state)
    if not isinstance(result,dict) or not guard.vm_uninterrupted(samples):
        return {'outcome':'focus_result_unknown'}
    return result

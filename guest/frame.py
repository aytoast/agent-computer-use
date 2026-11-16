"""Record a full window frame dynamically after revalidating its guest identity."""
def capture_tracked(agent,frames,target_id,capture_game_fn,clock):
    with agent.lock:
        frames.pop(target_id,None)
        if agent.state.paused:raise ValueError('Guest agent is paused')
        before=dict(agent.identity(target_id))
        hwnd,pid=before.get('hwnd'),before.get('pid')
        if type(hwnd) is not int or type(pid) is not int or hwnd<=0 or pid<=0:
            raise ValueError('Window identity is invalid')
        if before.get('class')=='UnityWndClass':
            metadata,png=capture_game_fn(hwnd,pid)
        else:
            res=agent.capture(target_id,None)
            if res.get('ok') is False:raise RuntimeError(res.get('error') or 'Window capture failed')
            png=res.get('png')
            metadata={k:v for k,v in res.items() if k!='png'}
        if dict(agent.identity(target_id))!=before:raise ValueError('Window identity changed during capture')
        width,height=metadata.get('width'),metadata.get('height')
        if type(width) is not int or type(height) is not int or width<=0 or height<=0 or not png:
            raise ValueError('Window capture is invalid')
        frames[target_id]={'captured_at':clock(),'size':(width,height),'identity':before}
        return metadata,png

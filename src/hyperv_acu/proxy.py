"""Guest-only MCP proxy for the current Hyper-V test VM."""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from hyperv_acu.bootstrap import ensure_paths
root = ensure_paths()

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy
from hyperv_acu import isolation as hyperv_isolation
from hyperv_acu import proxy_config as config

url, token = config.resolve_guest_endpoint(root)
def connection():
    return Client(StreamableHttpTransport(url, headers={'Authorization': 'Bearer ' + token}), mode='2026-07-28', timeout=45)

server = create_proxy(connection(), name='windows-hyperv', instructions=(
    'Only operate the configured Hyper-V guest. Respect guest takeover and pause. '
    'Never operate the host desktop. Observe fresh screenshots before and after actions. '
    'Use supported background actions only; stop on refused input or errors.'))
server.disable(names=set(config.DISABLED_TOOLS), components={'tool'})
input_path=root/'runtime'/'hyperv-input-block.json'

@server.tool(name='click')
async def click(target_id:str,point:list[int],button:str='left',clicks:int=1,consume_frame:bool=True,guard_input:bool=True):
    """Attempt one guest click after a tracked screenshot, stopping for VM user activity."""
    async with connection() as client:
        return await hyperv_isolation.click(client,target_id,point,input_path,button=button,clicks=clicks,consume_frame=consume_frame,guard_input=guard_input)

@server.tool(name='move')
async def move(target_id:str,point:list[int],hover_ms:int=0,consume_frame:bool=True,guard_input:bool=True):
    """Move guest cursor and optionally hover after a tracked screenshot."""
    async with connection() as client:
        return await hyperv_isolation.move(client,target_id,point,input_path,hover_ms=hover_ms,consume_frame=consume_frame,guard_input=guard_input)

@server.tool(name='drag')
async def drag(target_id:str,start_point:list[int],end_point:list[int],button:str='left',duration_ms:int=250,consume_frame:bool=True,guard_input:bool=True):
    """Drag guest cursor from start_point to end_point after a tracked screenshot."""
    async with connection() as client:
        return await hyperv_isolation.drag(client,target_id,start_point,end_point,input_path,button=button,duration_ms=duration_ms,consume_frame=consume_frame,guard_input=guard_input)

@server.tool(name='scroll')
async def scroll(target_id:str,dx:int=0,dy:int=0,point:list[int]|None=None,consume_frame:bool=True,guard_input:bool=True):
    """Scroll vertically and/or horizontally in the guest window."""
    async with connection() as client:
        return await hyperv_isolation.scroll(client,target_id,input_path,dx=dx,dy=dy,point=point,consume_frame=consume_frame,guard_input=guard_input)

@server.tool(name='focus_window')
async def focus_window(target_id:str):
    """Focus a verified guest window only when host and guest users are idle."""
    async with connection() as client:
        return await hyperv_isolation.focus(client,target_id)

if __name__ == '__main__':
    server.run(transport='stdio')

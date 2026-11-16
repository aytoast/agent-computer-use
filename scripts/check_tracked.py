import asyncio
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = root / 'src'
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from hyperv_acu.bootstrap import ensure_paths, load_settings
ensure_paths()
settings = load_settings(root)

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

python = settings.get('python_exe', sys.executable)

async def main():
    async with Client(StdioTransport(python,[str(src/'hyperv_acu'/'proxy.py')]),timeout=45) as client:
        names={item.name for item in await client.list_tools()}
        needed={'HyperVAgentInstance','Screenshot','focus_window','click'}
        print('required_tools',sorted(names & needed),flush=True)
        print('internal_tools_exposed',sorted(names & {'_VmForegroundClick','_VmCursorProbe'}),flush=True)
        status=(await client.call_tool('AgentStatus',{})).data
        print('status',json.dumps(status,ensure_ascii=False),flush=True)
        if status.get('paused') or status.get('task_active'):return
        windows=(await client.call_tool('WindowList',{'title':''})).data
        print('windows',json.dumps(windows,ensure_ascii=False),flush=True)
        if len(windows)!=1 or 'Screenshot' not in names:return
        result=await client.call_tool('Screenshot',{'target_id':windows[0]['target_id']})
        for item in result.content:
            if item.type=='text':print('capture',item.text,flush=True)
            if item.type=='image':
                import base64
                output=root.parent/'runtime'/'hyperv-tracked-check.png'
                output.write_bytes(base64.b64decode(item.data))
                print('image_saved',str(output),flush=True)

asyncio.run(main())

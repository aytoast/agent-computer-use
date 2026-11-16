import asyncio
import importlib.util
import ast
import json
import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ['HYPERV_DISABLE_PIP']='1'
os.environ.setdefault('HYPERV_VM_ID','00000000-0000-0000-0000-000000000001')

path=Path(__file__).resolve().parents[1]/'src'/'hyperv_acu'/'server.py'
spec=importlib.util.spec_from_file_location('hyperv_app_server',path)
server=importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class ScreenshotValidationTest(unittest.TestCase):
    def test_vm_inventory_lists_all_and_marks_control_configuration(self):
        inventory=[{'name':'Agent-VM','id':server.VM_ID,'state':'Running'},
                   {'name':'Other','id':'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee','state':'Off'}]
        result=server.format_vm_inventory(inventory,True)
        self.assertEqual([vm['name'] for vm in result],['Agent-VM','Other'])
        self.assertTrue(result[0]['guest_agent_connected'])
        self.assertFalse(result[1]['control_configured'])
        self.assertEqual(result[1]['state'],'Off')

    def test_metadata_and_image_are_both_required(self):
        text=SimpleNamespace(type='text',text=json.dumps({'width':100,'height':80}))
        image=SimpleNamespace(type='image')
        self.assertEqual(server.metadata_and_image(SimpleNamespace(content=[text,image])),(100,80))
        with self.assertRaises(RuntimeError):server.metadata_and_image(SimpleNamespace(content=[text]))
        with self.assertRaises(RuntimeError):server.metadata_and_image(SimpleNamespace(content=[image]))

    def test_invalid_dimensions_are_rejected(self):
        text=SimpleNamespace(type='text',text=json.dumps({'width':True,'height':80}))
        with self.assertRaises(RuntimeError):server.metadata_and_image(SimpleNamespace(content=[text,SimpleNamespace(type='image')]))

class ToolNamingTest(unittest.TestCase):
    def test_public_tools_use_lowercase_snake_case(self):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        names=[]
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and decorator.func.attr=='tool':
                        names.extend(k.value.value for k in decorator.keywords if k.arg=='name' and isinstance(k.value,ast.Constant))
        self.assertTrue(names)
        for name in names:
            with self.subTest(name=name):self.assertRegex(name,r'^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$')
        for expected in ('move','drag','wait','batch','exec_powershell'):
            self.assertIn(expected,names)


class NewToolsValidationTest(unittest.TestCase):
    def setUp(self):
        import hyperv_acu.app_policy as app_policy
        server._clients.clear()
        self._p1 = patch.object(server, 'VM_ID', '00000000-0000-0000-0000-000000000001')
        self._p2 = patch.object(app_policy, 'VM_ID', '00000000-0000-0000-0000-000000000001')
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()

    def test_scroll_delta_resolution(self):
        self.assertEqual(server._resolve_scroll_deltas(120,'vertical',0,0),(0,120))
        self.assertEqual(server._resolve_scroll_deltas(-120,'horizontal',0,0),(-120,0))
        self.assertEqual(server._resolve_scroll_deltas(0,'vertical',30,-60),(30,-60))
        with self.assertRaises(ValueError):server._resolve_scroll_deltas(0,'vertical',0,0)
        with self.assertRaises(ValueError):server._resolve_scroll_deltas(120,'diagonal',0,0)

    def test_button_and_clicks_validation(self):
        server._validate_button('left')
        server._validate_button('right')
        server._validate_button('middle')
        with self.assertRaises(ValueError):server._validate_button('x1')
        server._validate_clicks(1)
        server._validate_clicks(2)
        with self.assertRaises(ValueError):server._validate_clicks(3)

    def test_wait_validation_and_execution(self):
        with self.assertRaises(ValueError):asyncio.run(server.wait(0))
        with self.assertRaises(ValueError):asyncio.run(server.wait(61))
        with self.assertRaises(ValueError):asyncio.run(server.wait(True))
        res=asyncio.run(server.wait(0.01))
        self.assertEqual(res,{'waited_seconds':0.01})

    def test_batch_and_exec_powershell_dispatch(self):
        calls=[]
        class FakeClient:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def call_tool(self,name,args):
                calls.append((name,args))
                if name=='AgentStatus':return SimpleNamespace(data={'paused':False,'task_active':True},content=[])
                if name=='WindowList':return SimpleNamespace(data=[{'target_id':'win1','class':'Notepad'}],content=[])
                return SimpleNamespace(data={'ok':True},content=[SimpleNamespace(type='text',text='ok')])
        server._captures[server.VM_ID]={'target':'win1','time':time.monotonic(),'width':800,'height':600,'tracked':False}
        with patch.object(server,'connection',return_value=FakeClient()), \
             patch.object(server.input_guard,'require_clear'):
            batch_res=asyncio.run(server.batch(
                server.VM_ID,
                [
                    {'action':'move','x':100,'y':200,'hover_ms':10},
                    {'action':'double_click','x':100,'y':200,'button':'right','seconds':0.01},
                    {'action':'drag','start_x':10,'start_y':20,'end_x':50,'end_y':60},
                    {'action':'scroll','delta':-120,'direction':'horizontal','x':30,'y':40},
                    {'action':'wait','seconds':0.01},
                ],
                target_id='win1',
            ))
            self.assertEqual(batch_res['completed'],5)
            ps_res=asyncio.run(server.exec_powershell(server.VM_ID,'Write-Output hi',timeout_seconds=10))
            self.assertEqual(ps_res[0].text,'ok')
        tool_names=[name for name,_ in calls]
        self.assertIn('move',tool_names)
        self.assertIn('click',tool_names)
        self.assertIn('drag',tool_names)
        self.assertIn('scroll',tool_names)
        self.assertIn('ExecPowerShell',tool_names)

    def test_batch_reports_blocked_or_timeout_reasons(self):
        class FakeClient:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def call_tool(self,name,args):
                if name=='AgentStatus':return SimpleNamespace(data={'paused':False,'task_active':False},content=[])
                if name=='TaskContext':return SimpleNamespace(data={'active':True},content=[])
                if name=='WindowList':return SimpleNamespace(data=[{'target_id':'game1','class':'UnityWndClass'}],content=[])
                if name=='click':
                    return SimpleNamespace(content=[SimpleNamespace(type='text',text=json.dumps({'outcome':'user_active'}))])
                return SimpleNamespace(data={'ok':True},content=[])
        server._captures[server.VM_ID]={'target':'game1','time':time.monotonic(),'width':800,'height':600,'tracked':True}
        with patch.object(server,'connection',return_value=FakeClient()), \
             patch.object(server.input_guard,'require_clear'), \
             patch.object(server.input_guard,'start'), \
             patch.object(server.input_guard,'finish'):
            res=asyncio.run(server.batch(
                server.VM_ID,
                [{'action':'click','x':100,'y':200},{'action':'wait','seconds':0.01}],
                target_id='game1',
                task='Test task',should_complete_task=True,
            ))
        self.assertFalse(res['ok'])
        self.assertEqual(res['completed'],0)
        self.assertEqual(res['results'][0]['reason'],'user_active')

    def test_desktop_wide_screenshot_click_and_drag(self):
        calls=[]
        class FakeClient:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def call_tool(self,name,args):
                calls.append((name,args))
                if name=='AgentStatus':return SimpleNamespace(data={'paused':False,'task_active':True},content=[])
                if name=='HyperVDesktopScreenshot':
                    return SimpleNamespace(content=[
                        SimpleNamespace(type='text',text=json.dumps({'target_id':'desktop','width':1920,'height':1080})),
                        SimpleNamespace(type='image'),
                    ])
                return SimpleNamespace(content=[SimpleNamespace(type='text',text=json.dumps({'outcome':'delivered'}))])
        with patch.object(server,'connection',return_value=FakeClient()), \
             patch.object(server.input_guard,'require_clear'), \
             patch.object(server.input_guard,'observed',return_value=None):
            asyncio.run(server.screenshot(server.VM_ID))
            asyncio.run(server.click(server.VM_ID,500,500,coordinate_space='normalized_1000'))
            asyncio.run(server.screenshot(server.VM_ID))
            asyncio.run(server.drag(server.VM_ID,10,20,300,400))
            asyncio.run(server.screenshot(server.VM_ID))
            asyncio.run(server.type_text(server.VM_ID,'hello'))
        tool_names=[name for name,_ in calls]
        self.assertEqual(tool_names.count('HyperVDesktopScreenshot'),3)
        self.assertIn('click',tool_names)
        self.assertIn('drag',tool_names)
        self.assertIn('Type',tool_names)
        click_args=next(args for name,args in calls if name=='click')
        self.assertEqual(click_args['target_id'],'desktop')
        self.assertEqual(click_args['point'],[960,540])

    def test_ensure_vm_thumbnail_deduplicates_per_vm(self):
        spawned=[]
        class FakeProc:
            def __init__(self):self.alive=True
            def poll(self):return None if self.alive else 0
        def fake_popen(cmd,env=None,**kwargs):
            p=FakeProc()
            spawned.append((cmd,env,p))
            return p
        server._THUMBNAILS.clear()
        with patch.dict(os.environ,{'HYPERV_DISABLE_PIP':'0'},clear=False), \
             patch.object(server.subprocess,'Popen',side_effect=fake_popen):
            server._ensure_vm_thumbnail(server.VM_ID,'Agent-01')
            server._ensure_vm_thumbnail(server.VM_ID,'Agent-01')
            self.assertEqual(len(spawned),1)
            self.assertEqual(spawned[0][1]['HYPERV_VM_ID'],server.VM_ID)
            spawned[0][2].alive=False
            server._ensure_vm_thumbnail(server.VM_ID,'Agent-01')
            self.assertEqual(len(spawned),2)

if __name__=='__main__':unittest.main()

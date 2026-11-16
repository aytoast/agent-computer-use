import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from hyperv_acu import isolation as module

class Client:
    def __init__(self,probe=None):self.probe=probe or {'outcome':'moved_and_restored','before':[1,2],'target':[17,2],'after':[1,2]};self.calls=[]
    async def call_tool(self,name,args):
        self.calls.append(name)
        data={'AgentStatus':{'paused':False,'task_active':True},'HyperVAgentInstance':{'instance_id':'abc'},'_VmCursorProbe':self.probe,'_VmForegroundClick':{'outcome':'delivered'},'_VmFocusWindow':{'outcome':'focused'}}[name]
        return SimpleNamespace(data=data)

class IsolationTests(unittest.TestCase):
    def test_focus_refuses_when_user_has_vm_foreground(self):
        c=Client();state={'idle_seconds':0,'held':False,'vm_foreground':True,'tick':1,'cursor':(2,3),'foreground':4}
        result=asyncio.run(module.focus(c,'target',read_state=lambda:state))
        self.assertEqual(result['outcome'],'vm_foreground')
        self.assertNotIn('_VmFocusWindow',c.calls)

    def test_focus_allows_unrelated_host_activity(self):
        c=Client();state={'idle_seconds':0,'held':True,'vm_foreground':False,'tick':1,'cursor':(2,3),'foreground':4}
        result=asyncio.run(module.focus(c,'target',read_state=lambda:state))
        self.assertEqual(result['outcome'],'focused')

    def test_foreground_click_allows_host_activity_without_repeating_probe(self):
        with tempfile.TemporaryDirectory() as d:
            blocked=Path(d)/'input.json';c=Client();state={'idle_seconds':0,'held':True,'vm_foreground':False,'tick':1,'cursor':(2,3),'foreground':4}
            blocked.write_text(json.dumps({'state':'uncertain','target':'target'}))
            with self.assertRaises(ValueError):asyncio.run(module.click(c,'target',[3,4],blocked,read_state=lambda:state))
            self.assertNotIn('_VmForegroundClick',c.calls)
            blocked.unlink()
            result=asyncio.run(module.click(c,'target',[3,4],blocked,read_state=lambda:state))
            self.assertEqual(result['outcome'],'delivered')
            self.assertEqual(json.loads(blocked.read_text())['state'],'awaiting_observation')
            self.assertNotIn('_VmCursorProbe',c.calls)

    def test_foreground_click_respects_vm_takeover(self):
        with tempfile.TemporaryDirectory() as d:
            c=Client();state={'vm_foreground':True,'idle_seconds':0,'tick':1}
            result=asyncio.run(module.click(c,'target',[3,4],Path(d)/'input',read_state=lambda:state))
            self.assertEqual(result['outcome'],'vm_foreground')
            self.assertNotIn('_VmForegroundClick',c.calls)
            state['idle_seconds']=15
            result=asyncio.run(module.click(c,'target',[3,4],Path(d)/'input',read_state=lambda:state))
            self.assertEqual(result['outcome'],'delivered')

if __name__=='__main__':unittest.main()

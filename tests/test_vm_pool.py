import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hyperv_acu import app_policy, proxy_config, vm_pool


class VmPoolTest(unittest.TestCase):
    def test_register_and_unregister_managed_vms_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e1 = vm_pool.register_vm(
                {'id': '11111111-1111-1111-1111-111111111111', 'name': 'agent-1', 'mac': '00-15-5D-00-00-01', 'mode': 'ephemeral'},
                root=root, max_vms=2,
            )
            self.assertEqual(e1['mac'], '00155D000001')
            vm_pool.register_vm(
                {'id': '22222222-2222-2222-2222-222222222222', 'name': 'agent-2', 'mac': '00:15:5D:00:00:02', 'mode': 'persistent'},
                root=root, max_vms=2,
            )
            with self.assertRaises(ValueError):
                vm_pool.register_vm(
                    {'id': '33333333-3333-3333-3333-333333333333', 'name': 'agent-3', 'mac': '00155D000003', 'mode': 'ephemeral'},
                    root=root, max_vms=2,
                )
            self.assertEqual(vm_pool.get_managed_vm('22222222-2222-2222-2222-222222222222', root=root)['mode'], 'persistent')
            removed = vm_pool.unregister_vm('11111111-1111-1111-1111-111111111111', root=root)
            self.assertIsNotNone(removed)
            self.assertNotIn('11111111-1111-1111-1111-111111111111', vm_pool.list_managed_ids(root=root))

    def test_invalid_mode_and_unmanaged_vm_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                vm_pool.register_vm({'id': '111', 'mac': '00155D000001', 'mode': 'invalid'}, root=root)
            with patch.dict(os.environ, {'HYPERV_VM_ID': ''}, clear=False):
                with self.assertRaises(ValueError):
                    vm_pool.get_managed_vm('99999999-9999-9999-9999-999999999999', root=root)

    def test_proxy_config_resolves_specific_mac(self):
        entries = [
            {'IPAddress': '172.23.95.24', 'LinkLayerAddress': '00-15-5D-00-00-10'},
            {'IPAddress': '172.23.95.25', 'LinkLayerAddress': '00-15-5D-00-00-20'},
        ]
        self.assertEqual(proxy_config.resolve_guest(entries, expected_mac='00155D000020'), '172.23.95.25')

    def test_require_vm_accepts_pool_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vm_pool.register_vm({'id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'mac': '00155D00000A', 'mode': 'ephemeral'}, root=root)
            with patch.object(app_policy, 'VM_ID', ''), patch.object(vm_pool, 'ROOT', root):
                app_policy.require_vm('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
                with self.assertRaises(ValueError):
                    app_policy.require_vm('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')

    def test_lifecycle_tools_registered_in_server(self):
        server_path = Path(__file__).resolve().parents[1] / 'src' / 'hyperv_acu' / 'server.py'
        tree = ast.parse(server_path.read_text(encoding='utf-8'))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and getattr(dec.func, 'attr', None) == 'tool':
                        for kw in dec.keywords:
                            if kw.arg == 'name' and isinstance(kw.value, ast.Constant):
                                names.add(kw.value.value)
        for expected in ('create_vm', 'destroy_vm', 'start_vm', 'stop_vm', 'checkpoint_vm', 'restore_vm'):
            self.assertIn(expected, names)


if __name__ == '__main__':
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hyperv_acu import proxy_config as config

class HyperVProxyConfigTest(unittest.TestCase):
    def test_private_ipv4_accepted(self):
        self.assertEqual(config.guest_url('172.23.95.24'), 'http://172.23.95.24:18777/mcp')

    def test_nonprivate_and_invalid_targets_rejected(self):
        for target in ('127.0.0.1', '8.8.8.8', '::1', 'example.com', '172.23.95.24/path'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                config.guest_url(target)

    def test_privileged_foreground_input_is_not_exposed(self):
        self.assertTrue({'_VmForegroundClick', '_VmCursorProbe', 'VmOverlayTiming'}.issubset(config.DISABLED_TOOLS))

    def test_resolves_only_the_expected_virtual_adapter(self):
        entries=[{'IPAddress':'172.23.95.24','LinkLayerAddress':'00-15-5D-00-00-01'},
                 {'IPAddress':'172.23.95.99','LinkLayerAddress':'00-15-5D-11-22-33'}]
        with patch.object(config, 'EXPECTED_MAC', '00155D000001'):
            self.assertEqual(config.resolve_guest(entries),'172.23.95.24')
            with self.assertRaises(ValueError):config.resolve_guest(entries[1:])
            with self.assertRaises(ValueError):config.resolve_guest(entries+[entries[0]])

    def test_read_guest_token_from_dotenv_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_file = root / 'tok.txt'
            token_file.write_text('secret-token\n', encoding='utf-8')
            (root / '.env').write_text(f'HYPERV_GUEST_TOKEN_PATH={token_file}\n', encoding='utf-8')
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('HYPERV_GUEST_TOKEN', None)
                os.environ.pop('HYPERV_GUEST_TOKEN_PATH', None)
                self.assertEqual(config.read_guest_token(root), 'secret-token')

    def test_read_guest_token_prefers_env_over_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('HYPERV_GUEST_TOKEN=from-file\n', encoding='utf-8')
            with patch.dict(os.environ, {'HYPERV_GUEST_TOKEN': 'from-env'}):
                self.assertEqual(config.read_guest_token(root), 'from-env')

    def test_read_guest_token_requires_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('HYPERV_GUEST_TOKEN', None)
                os.environ.pop('HYPERV_GUEST_TOKEN_PATH', None)
                with self.assertRaises(ValueError):
                    config.read_guest_token(Path(tmp))

    def test_resolve_guest_url_falls_back_to_broker_when_arp_empty(self):
        with patch('hyperv_acu.proxy_config.subprocess.run') as run_mock:
            run_mock.return_value = type('P', (), {'stdout': '[]'})()
            with patch('hyperv_acu.proxy_config._resolve_via_broker', return_value='172.23.10.5') as broker_mock:
                url = config.resolve_guest_url(expected_mac='00155D000001', guest_port=18777)
                self.assertEqual(url, 'http://172.23.10.5:18777/mcp')
                broker_mock.assert_called_once_with(expected_mac='00155D000001')

if __name__ == '__main__':
    unittest.main()

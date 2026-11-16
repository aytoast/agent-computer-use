"""Managed Hyper-V VM pool registry for multi-VM ephemeral and persistent sandboxes."""
import json
import os
import tempfile
from pathlib import Path
from hyperv_acu.bootstrap import ROOT, load_settings

VALID_MODES = frozenset({'ephemeral', 'persistent'})


def pool_path(root: Path | None = None) -> Path:
    return Path(root if root is not None else ROOT) / 'runtime' / 'vms.json'


def load_pool(root: Path | None = None) -> dict:
    path = pool_path(root)
    if not path.is_file():
        return {'vms': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('vms'), dict):
        raise ValueError('Managed VM pool registry is invalid')
    return data


def save_pool(pool: dict, root: Path | None = None) -> None:
    path = pool_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix='vms.', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(pool, handle, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def register_vm(entry: dict, root: Path | None = None, max_vms: int | None = None) -> dict:
    if not isinstance(entry, dict):
        raise ValueError('VM entry must be an object')
    vm_id = str(entry.get('id', '')).strip().lower()
    if not vm_id:
        raise ValueError('VM id is required')
    mode = str(entry.get('mode', 'ephemeral')).strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f'Unsupported VM mode: {mode}')
    mac = str(entry.get('mac', '')).replace('-', '').replace(':', '').strip().upper()
    if len(mac) != 12:
        raise ValueError('A 12-hex-digit VM MAC address is required')

    settings = load_settings(root)
    limit = int(max_vms if max_vms is not None else settings.get('max_managed_vms', 4))
    pool = load_pool(root)
    vms = pool['vms']
    if vm_id not in vms and len(vms) >= limit:
        raise ValueError(f'Managed VM limit reached ({limit})')

    record = {
        'id': vm_id,
        'name': str(entry.get('name', vm_id)).strip() or vm_id,
        'mac': mac,
        'mode': mode,
        'port': int(entry.get('port', settings.get('guest_port', 18777))),
        'base_vhdx': str(entry.get('base_vhdx', '')),
        'diff_vhdx': str(entry.get('diff_vhdx', '')),
        'token_path': str(entry.get('token_path', '')),
    }
    vms[vm_id] = record
    save_pool(pool, root)
    return record


def unregister_vm(vm_id: str, root: Path | None = None) -> dict | None:
    key = str(vm_id).strip().lower()
    pool = load_pool(root)
    removed = pool['vms'].pop(key, None)
    if removed is not None:
        save_pool(pool, root)
    return removed


def get_managed_vm(vm_id: str, root: Path | None = None) -> dict:
    if not isinstance(vm_id, str) or not vm_id.strip():
        raise ValueError('Select a managed Hyper-V VM by its exact ID')
    key = vm_id.strip().lower()
    pool = load_pool(root)
    if key in pool['vms']:
        return pool['vms'][key]
    settings = load_settings(root)
    default_id = str(os.environ.get('HYPERV_VM_ID') or settings.get('vm_id', '')).strip().lower()
    if default_id and key == default_id:
        return {
            'id': default_id,
            'name': str(settings.get('vm_name', 'Agent-VM')),
            'mac': str(os.environ.get('HYPERV_VM_MAC') or settings.get('vm_mac', '')).replace('-', '').replace(':', '').upper(),
            'mode': 'persistent',
            'port': int(settings.get('guest_port', 18777)),
            'token_path': '',
        }
    raise ValueError('Select a managed Hyper-V VM by its exact ID')


def list_managed_ids(root: Path | None = None) -> set[str]:
    ids = set(load_pool(root)['vms'].keys())
    default_id = str(os.environ.get('HYPERV_VM_ID') or load_settings(root).get('vm_id', '')).strip().lower()
    if default_id:
        ids.add(default_id)
    return ids

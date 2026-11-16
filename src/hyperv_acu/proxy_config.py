import ipaddress
import json
import os
import subprocess
from pathlib import Path
from hyperv_acu.bootstrap import ROOT, load_settings

_SETTINGS = load_settings()
DISABLED_TOOLS = frozenset({'_VmForegroundClick', '_VmForegroundMove', '_VmForegroundDrag', '_VmForegroundScroll', '_VmCursorProbe', '_VmFocusWindow', 'VmOverlayTiming', 'HyperVDesktopScreenshot'})
EXPECTED_MAC = str(os.environ.get('HYPERV_VM_MAC') or _SETTINGS.get('vm_mac', '')).replace('-', '').replace(':', '').upper()
GUEST_PORT = int(_SETTINGS.get('guest_port', 18777))
_NEIGHBOR_QUERY = (
    "$n=Get-NetNeighbor -AddressFamily IPv4 -InterfaceAlias 'vEthernet (Default Switch)' "
    "-ErrorAction Stop | Select-Object IPAddress,LinkLayerAddress; ConvertTo-Json -InputObject @($n) -Compress"
)


def _read_dotenv(root: Path) -> dict[str, str]:
    env_file = Path(root) / '.env'
    if not env_file.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def read_guest_token(root: Path) -> str:
    """Resolve the guest agent bearer token from env or .env."""
    env = _read_dotenv(root)
    token = (os.environ.get('HYPERV_GUEST_TOKEN') or env.get('HYPERV_GUEST_TOKEN') or '').strip()
    if token:
        return token
    token_path = (os.environ.get('HYPERV_GUEST_TOKEN_PATH') or env.get('HYPERV_GUEST_TOKEN_PATH') or '').strip()
    if not token_path:
        raise ValueError('HYPERV_GUEST_TOKEN or HYPERV_GUEST_TOKEN_PATH is not configured')
    path = Path(token_path)
    if not path.is_absolute():
        path = Path(root) / path
    if not path.is_file():
        raise FileNotFoundError(f'Guest agent token file not found: {path}')
    token = path.read_text(encoding='utf-8').strip()
    if not token:
        raise ValueError(f'Guest agent token file is empty: {path}')
    return token

def resolve_guest(entries, expected_mac: str | None = None):
    target_mac = str(expected_mac if expected_mac is not None else EXPECTED_MAC).replace('-', '').replace(':', '').upper()
    if not target_mac:
        raise ValueError('Configured Hyper-V guest MAC is missing')
    matches=[entry.get('IPAddress') for entry in entries if isinstance(entry,dict)
             and str(entry.get('LinkLayerAddress','')).replace('-','').replace(':','').upper()==target_mac]
    if len(matches)!=1:
        raise ValueError('The configured Hyper-V guest address is unavailable or ambiguous')
    guest_url(matches[0])
    return matches[0]

def guest_url(address: str, port: int | None = None) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise ValueError('Invalid VM address') from exc
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError('Expected a private, non-loopback guest IPv4 address')
    return f'http://{ip}:{port or GUEST_PORT}/mcp'


def _resolve_via_broker(expected_mac: str | None = None) -> str:
    script = ROOT / 'scripts' / 'admin-request.ps1'
    if not script.is_file():
        raise ValueError('No matching Hyper-V guest IPv4 neighbor found')
    payload = json.dumps({'mac': expected_mac or EXPECTED_MAC})
    proc = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Operation', 'get_vm_ip', '-PayloadJson', payload],
        capture_output=True, text=True, timeout=15, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    reply = json.loads(proc.stdout)
    ip = reply.get('ip')
    if not ip:
        raise ValueError('Hyper-V broker did not return a guest IP')
    return str(ip)


def resolve_guest_url(expected_mac: str | None = None, guest_port: int | None = None) -> str:
    """Query Hyper-V Default Switch neighbors and return the guest MCP URL."""
    proc = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', _NEIGHBOR_QUERY],
        capture_output=True, text=True, timeout=15, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    try:
        ip = resolve_guest(json.loads(proc.stdout), expected_mac=expected_mac)
    except ValueError:
        ip = _resolve_via_broker(expected_mac=expected_mac)
    return guest_url(ip, port=guest_port)


def resolve_guest_endpoint(root: Path | None = None) -> tuple[str, str]:
    """Return (guest_url, bearer_token) for the configured Hyper-V guest."""
    base = Path(root) if root is not None else ROOT
    return resolve_guest_url(), read_guest_token(base)

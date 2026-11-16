"""Shared path, vendor dependency, and settings bootstrap."""
import json
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
ROOT = SRC_DIR.parent
VENDOR_FASTMCP = ROOT / 'vendor' / 'fastmcp-4.1.8'


def ensure_paths() -> Path:
    """Ensure src/ and vendored fastmcp are on sys.path; return repo root."""
    if not VENDOR_FASTMCP.is_dir():
        raise RuntimeError('Hyper-V dependencies are missing')
    for path in (str(SRC_DIR), str(VENDOR_FASTMCP)):
        if path not in sys.path:
            sys.path.insert(0, path)
    return ROOT


def load_settings(root: Path | None = None) -> dict:
    """Load config/settings.json merged with optional config/settings.local.json."""
    base = Path(root) if root is not None else ROOT
    settings_path = base / 'config' / 'settings.json'
    data = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.is_file() else {}
    local_path = base / 'config' / 'settings.local.json'
    if local_path.is_file():
        data.update(json.loads(local_path.read_text(encoding='utf-8')))
    return data

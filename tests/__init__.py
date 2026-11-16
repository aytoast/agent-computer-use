import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / 'src'), str(_ROOT / 'guest')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

"""Put the toolkit's layers on sys.path. Import once, before importing any toolkit module.

    sys.path.insert(0, str(REPO_ROOT / "code"))
    import toolkit_path  # noqa: F401
"""
import sys
from pathlib import Path

LAYERS = ("core", "clients", "builders", "validation")

_HERE = Path(__file__).resolve().parent
for _layer in LAYERS:
    _path = str(_HERE / _layer)
    if _path not in sys.path:
        sys.path.insert(0, _path)

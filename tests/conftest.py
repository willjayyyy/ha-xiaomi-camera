"""Make the add-on's bridge package importable from the tests."""

import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "addon" / "rootfs" / "app"
sys.path.insert(0, str(_APP))

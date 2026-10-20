"""Test package.

Adds ``src/`` to ``sys.path`` so the modules can be imported the same way the
application imports them (``import analysis``, ``from utils.llm import ...``).
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

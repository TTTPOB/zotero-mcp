"""Check the source tree using a temporary uv environment, without installing it."""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for args in (("ruff", "check", "."), ("ruff", "format", "--check", "."), ("pytest", "-q")):
    result = subprocess.run([sys.executable, "-m", *args], cwd=root, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)

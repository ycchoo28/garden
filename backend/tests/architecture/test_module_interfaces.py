"""The command is also the seam used by Scaffold violation tests."""

import subprocess
import sys
from pathlib import Path


def test_backend_architecture_command() -> None:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_backend_architecture.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

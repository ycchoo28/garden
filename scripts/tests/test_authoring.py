"""The authoring command rejects a prohibited character and accepts a plain dash."""

import shutil
import subprocess
import sys
from pathlib import Path


def test_authoring_command(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "check_authoring.py"
    (tmp_path / "scripts").mkdir()
    shutil.copy(source, tmp_path / "scripts")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    file = tmp_path / "note.md"
    file.write_text("allowed - dash\n")
    command = [sys.executable, str(tmp_path / "scripts/check_authoring.py")]
    good = subprocess.run(command, capture_output=True, text=True, check=False)
    assert good.returncode == 0, good.stdout
    file.write_text("prohibited " + chr(0x2014) + " dash\n")
    bad = subprocess.run(command, capture_output=True, text=True, check=False)
    assert bad.returncode != 0 and "note.md:1: authoring-dash" in bad.stdout

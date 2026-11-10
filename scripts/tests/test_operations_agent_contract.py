"""Prove the operation declaration, agent architecture checks and API check command."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_operation_requires_explicit_confirmation_value() -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-c",
            "from app.shared.operations import op; op('test.item.read', 'Read an item.')",
        ],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "confirm" in result.stderr
    for confirmation in ("True", "False"):
        allowed = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-c",
                "from app.shared.operations import op; assert op('test.item.read', "
                "'Read an item.', confirm="
                + confirmation
                + ')["openapi_extra"]["x-agent"]["confirm"] is '
                + confirmation,
            ],
            cwd=ROOT / "backend",
            capture_output=True,
            text=True,
            check=False,
        )
        assert allowed.returncode == 0, allowed.stdout + allowed.stderr


def _agent_tree(tmp_path: Path) -> Path:
    root = tmp_path / "garden"
    shutil.copytree(
        ROOT / "agent",
        root / "agent",
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", "*.pyc", "dist", "var", ".pytest_cache", ".ruff_cache"
        ),
    )
    shutil.copytree(ROOT / "contracts", root / "contracts")
    return root


def _agent_check(root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"PYTHONPATH": str(root / "agent/src")}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--confcutdir=tests/architecture",
            "tests/architecture/test_module_interfaces.py",
            "-q",
        ],
        cwd=root / "agent",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_agent_checks_discover_packages_import_forms_and_skill_operations(tmp_path: Path) -> None:
    root = _agent_tree(tmp_path)
    agent = root / "agent"
    baseline = _agent_check(root)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    new_package = agent / "src/garden/new_package"
    new_package.mkdir()
    (new_package / "module.py").write_text("value = 1\n")
    bad = _agent_check(root)
    assert bad.returncode != 0 and "agent-layer-exhaustiveness" in bad.stdout
    (new_package / "module.py").unlink()
    new_package.rmdir()
    good = _agent_check(root)
    assert good.returncode == 0, good.stdout + good.stderr

    forms = "\n".join(
        (
            "import garden.shared.errors",
            "from garden.shared.errors import GardenError",
            "from ..shared.errors import GardenError",
            "def call():\n    from garden.shared.errors import GardenError",
        )
    )
    bad_imports = agent / "src/garden/cli/planted_imports.py"
    bad_imports.write_text(forms + "\n")
    bad = _agent_check(root)
    assert bad.returncode != 0
    assert "agent-public-interface" in bad.stdout
    bad_imports.unlink()

    allowed = agent / "src/garden/cli/permitted.py"
    allowed.write_text("from garden.shared import GardenError\n")
    good = _agent_check(root)
    assert good.returncode == 0, good.stdout + good.stderr
    allowed.unlink()

    skill = agent / "src/garden/skills/garden-basics/SKILL.md"
    original = skill.read_text()
    skill.write_text(original + "\nStale operation: `missing.record.remove`.\n")
    bad = _agent_check(root)
    assert bad.returncode != 0
    assert "agent-skill-operation-contract" in bad.stdout
    assert "missing.record.remove" in bad.stdout


def _api_check_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "garden"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/check_api.py", root / "scripts/check_api.py")
    (root / "backend").mkdir()
    (root / "frontend/src/api/generated").mkdir(parents=True)
    (root / "contracts").mkdir()
    (root / "contracts/openapi.json").write_text('{"paths": {}}\n')
    generated_file = root / "frontend/src/api/generated/client.ts"
    generated_file.write_text("client\n")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[-1]).write_text('{\\\"paths\\\": {}}\\n')\n"
    )
    bunx = bin_dir / "bunx"
    bunx.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'client.ts').write_text('client\\n')\n"
    )
    uv.chmod(0o755)
    bunx.chmod(0o755)
    return root, root / "contracts/openapi.json", generated_file


def test_api_check_reports_stale_generated_files_without_mutating_them(tmp_path: Path) -> None:
    root, contract, generated = _api_check_tree(tmp_path)
    env = os.environ | {"PATH": str(root / "bin") + os.pathsep + os.environ["PATH"]}
    command = [sys.executable, str(root / "scripts/check_api.py")]
    good = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert good.returncode == 0, good.stdout + good.stderr

    contract.write_text('{"paths": {"/stale": {}}}\n')
    generated.write_text("stale client\n")
    before_contract = contract.read_bytes()
    before_client = generated.read_bytes()
    bad = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert bad.returncode != 0
    assert "contracts/openapi.json" in bad.stderr
    assert "frontend/src/api/generated/client.ts" in bad.stderr
    assert contract.read_bytes() == before_contract
    assert generated.read_bytes() == before_client

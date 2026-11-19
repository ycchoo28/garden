"""Reusable WSL demo acceptance runner with preflight, warm caches, and HTTP OIDC approval.

Replaces ad-hoc remote PowerShell/Task Scheduler/Edge CDP harnesses with a single
checked-in runner that:
  1. Preflights host RAM, disk, Docker, leftover containers, and demo loopback ports.
  2. Isolates HOME and install state per pass while reusing package caches (uv, npm, bun).
  3. Approves the local fake OIDC sign-in over HTTP via scripts/oidc_approve.py.
  4. Distinguishes infrastructure failures (exit code 2) from product failures (exit code 1).
  5. Writes structured summary.json + run.log artifacts and guarantees cleanup.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import oidc_approve  # noqa: E402

EXIT_OK = 0
EXIT_PRODUCT = 1
EXIT_INFRA = 2

DEFAULT_DEMO_PORTS: tuple[int, ...] = (55000, 55173, 55433, 55082, 55084, 55085)
DEFAULT_MIN_FREE_RAM_MB = 1536
DEFAULT_MIN_FREE_DISK_MB = 4096
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "garden-wsl-accept"
FALLBACK_CACHE_DIR = Path("/tmp/garden-wsl-accept-cache")


def resolve_writable_cache_dir(
    preferred: Path,
    fallback: Path = FALLBACK_CACHE_DIR,
) -> Path:
    for candidate in (preferred, fallback):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    return fallback


@dataclass
class CheckResult:
    ok: bool
    detail: str
    value: object = None


@dataclass
class PreflightReport:
    ok: bool
    checks: dict[str, CheckResult]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "ok" if self.ok else "infra_error",
            "ok": self.ok,
            "checks": {name: asdict(result) for name, result in self.checks.items()},
        }


@dataclass
class PassRecord:
    name: str
    kind: str
    ok: bool
    exit_code: int
    failure_category: str | None
    duration_seconds: float
    log_path: str
    approval: dict[str, object] | None = None
    cleanup_exit_codes: dict[str, int] = field(default_factory=dict)
    detail: str = ""


def read_available_ram_mb(meminfo_path: Path = Path("/proc/meminfo")) -> int | None:
    if not meminfo_path.is_file():
        return None
    values: dict[str, int] = {}
    for line in meminfo_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":") and parts[1].isdigit():
            values[parts[0][:-1]] = int(parts[1])
    kb = values.get("MemAvailable", values.get("MemFree"))
    if kb is None:
        return None
    return kb // 1024


def check_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def check_docker(timeout: float = 15.0) -> CheckResult:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return CheckResult(ok=False, detail="docker CLI not found on PATH.")
    try:
        info = subprocess.run(
            [docker_bin, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(ok=False, detail=f"docker info timed out after {timeout:.0f}s.")
    if info.returncode != 0:
        err = (info.stderr or info.stdout).strip().splitlines()[:1]
        return CheckResult(ok=False, detail=f"docker daemon unavailable: {' '.join(err)}")

    try:
        compose = subprocess.run(
            [docker_bin, "compose", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            ok=False, detail=f"docker compose version timed out after {timeout:.0f}s."
        )
    if compose.returncode != 0:
        return CheckResult(ok=False, detail="docker compose plugin unavailable.")

    return CheckResult(
        ok=True,
        detail=f"Docker {info.stdout.strip()} / Compose {compose.stdout.strip()}",
        value={"server": info.stdout.strip(), "compose": compose.stdout.strip()},
    )


def check_leftover_containers(timeout: float = 15.0) -> CheckResult:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return CheckResult(ok=False, detail="docker CLI not found on PATH.")
    try:
        proc = subprocess.run(
            [
                docker_bin,
                "ps",
                "-a",
                "--filter",
                "name=garden-demo",
                "--format",
                "{{.Names}} ({{.Status}})",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(ok=False, detail=f"docker ps timed out after {timeout:.0f}s.")
    if proc.returncode != 0:
        return CheckResult(ok=False, detail="Could not query Docker containers.")
    leftovers = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if leftovers:
        return CheckResult(
            ok=False,
            detail=f"Leftover garden-demo containers found: {', '.join(leftovers)}",
            value=leftovers,
        )
    return CheckResult(ok=True, detail="No leftover garden-demo containers.", value=[])


def run_preflight(
    *,
    repo_root: Path = REPO_ROOT,
    min_free_ram_mb: int = DEFAULT_MIN_FREE_RAM_MB,
    min_free_disk_mb: int = DEFAULT_MIN_FREE_DISK_MB,
    ports: tuple[int, ...] = DEFAULT_DEMO_PORTS,
    check_docker_fn=check_docker,
    check_containers_fn=check_leftover_containers,
    check_port_fn=check_port_free,
    read_ram_fn=read_available_ram_mb,
) -> PreflightReport:
    checks: dict[str, CheckResult] = {}

    ram_mb = read_ram_fn()
    if ram_mb is None:
        checks["ram"] = CheckResult(ok=True, detail="Skipped (/proc/meminfo unavailable).")
    elif ram_mb < min_free_ram_mb:
        checks["ram"] = CheckResult(
            ok=False,
            detail=f"Available RAM {ram_mb} MiB is below minimum {min_free_ram_mb} MiB.",
            value=ram_mb,
        )
    else:
        checks["ram"] = CheckResult(
            ok=True,
            detail=f"{ram_mb} MiB available (minimum {min_free_ram_mb} MiB).",
            value=ram_mb,
        )

    repo_free_mb = shutil.disk_usage(repo_root).free // (1024 * 1024)
    tmp_free_mb = shutil.disk_usage(Path("/tmp")).free // (1024 * 1024)
    disk_values: dict[str, int] = {"repo_mb": repo_free_mb, "tmp_mb": tmp_free_mb}
    min_found = min(repo_free_mb, tmp_free_mb)
    if is_wsl() and Path("/mnt/c").is_dir():
        win_c_free_mb = shutil.disk_usage(Path("/mnt/c")).free // (1024 * 1024)
        disk_values["win_c_mb"] = win_c_free_mb
        min_found = min(min_found, win_c_free_mb)
    if min_found < min_free_disk_mb:
        parts = ", ".join(f"{k}={v} MiB" for k, v in disk_values.items())
        checks["disk"] = CheckResult(
            ok=False,
            detail=f"Low disk space: {parts} (minimum {min_free_disk_mb} MiB).",
            value=disk_values,
        )
    else:
        parts = ", ".join(f"{k}={v} MiB" for k, v in disk_values.items())
        checks["disk"] = CheckResult(
            ok=True,
            detail=f"{parts} free.",
            value=disk_values,
        )

    docker_res = check_docker_fn()
    checks["docker"] = docker_res
    if docker_res.ok:
        checks["containers"] = check_containers_fn()
    else:
        checks["containers"] = CheckResult(
            ok=False, detail="Skipped because Docker is unavailable."
        )

    busy_ports = [port for port in ports if not check_port_fn(port)]
    if busy_ports:
        checks["ports"] = CheckResult(
            ok=False,
            detail=f"Demo loopback ports already in use: {', '.join(map(str, busy_ports))}",
            value=busy_ports,
        )
    else:
        checks["ports"] = CheckResult(
            ok=True,
            detail=f"All demo ports free ({', '.join(map(str, ports))}).",
            value=[],
        )

    return PreflightReport(ok=all(c.ok for c in checks.values()), checks=checks)


def is_wsl(proc_version_path: Path = Path("/proc/version")) -> bool:
    if not proc_version_path.is_file():
        return False
    text = proc_version_path.read_text(encoding="utf-8", errors="replace").lower()
    return "microsoft" in text or "wsl" in text


def find_windows_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path("/mnt/c/Windows/System32") / name,
        Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0") / name,
        Path("/mnt/c/Windows") / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def list_wsl_interop_sockets(run_wsl_dir: Path = Path("/run/WSL")) -> list[str]:
    candidates: list[str] = []
    existing = os.environ.get("WSL_INTEROP")
    if existing and Path(existing).exists():
        candidates.append(existing)
    if run_wsl_dir.is_dir():
        sockets = sorted(
            run_wsl_dir.glob("*_interop"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        for sock in sockets:
            s = str(sock)
            if s not in candidates:
                candidates.append(s)
    # Prefer non-PID-1 relay sockets over 1_interop, keeping 1_interop as last fallback.
    non_init = [s for s in candidates if Path(s).name != "1_interop"]
    init_only = [s for s in candidates if Path(s).name == "1_interop"]
    return non_init + init_only


def _probe_interop_socket(
    probe_cmd: list[str],
    socket_path: str | None,
    *,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    probe_env = dict(os.environ)
    if socket_path:
        probe_env["WSL_INTEROP"] = socket_path
    probe_cwd = "/mnt/c" if Path("/mnt/c").is_dir() else None
    try:
        proc = subprocess.run(
            probe_cmd,
            cwd=probe_cwd,
            env=probe_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:.0f}s"
    if proc.returncode == 0 and "WSL_INTEROP_OK" in proc.stdout:
        return True, "ok"
    detail = (proc.stderr or proc.stdout).strip()[:200]
    return False, f"rc={proc.returncode}: {detail}"


def find_wsl_interop_socket(run_wsl_dir: Path = Path("/run/WSL")) -> str | None:
    candidates = list_wsl_interop_sockets(run_wsl_dir)
    if not candidates:
        return None
    cmd = find_windows_tool("cmd.exe")
    if cmd:
        probe_cmd = [cmd, "/c", "echo", "WSL_INTEROP_OK"]
        for sock in candidates:
            ok, _ = _probe_interop_socket(probe_cmd, sock, timeout=5.0)
            if ok:
                return sock
    return candidates[0]


def run_browser_check(*, timeout: float = 15.0) -> dict[str, object]:
    wsl = is_wsl()
    powershell = find_windows_tool("powershell.exe")
    cmd = find_windows_tool("cmd.exe")
    explorer = find_windows_tool("explorer.exe")

    interop_ok = False
    interop_socket: str | None = None
    interop_detail = "Not in WSL; Windows browser interop check skipped."
    if wsl:
        probe_cmd: list[str] | None = None
        if cmd:
            probe_cmd = [cmd, "/c", "echo", "WSL_INTEROP_OK"]
        elif powershell:
            probe_cmd = [powershell, "-NoProfile", "-Command", "Write-Output WSL_INTEROP_OK"]
        if probe_cmd is None:
            interop_detail = "Neither cmd.exe nor powershell.exe found for WSL Windows interop."
        else:
            candidates = list_wsl_interop_sockets() or [None]
            per_try = min(timeout, 6.0) if len(candidates) > 1 else timeout
            last_err = ""
            for sock in candidates:
                ok, msg = _probe_interop_socket(probe_cmd, sock, timeout=per_try)
                if ok:
                    interop_ok = True
                    interop_socket = sock
                    tool_name = Path(probe_cmd[0]).name
                    interop_detail = f"WSL->Windows interop succeeded via {tool_name}."
                    break
                last_err = f"{sock or 'default'}: {msg}"
            if not interop_ok:
                interop_detail = f"WSL->Windows interop failed ({last_err})."

    ok = (not wsl) or (interop_ok and bool(powershell or cmd or explorer))
    return {
        "ok": ok,
        "is_wsl": wsl,
        "tools": {
            "powershell.exe": powershell,
            "cmd.exe": cmd,
            "explorer.exe": explorer,
        },
        "interop_ok": interop_ok,
        "interop_socket": interop_socket,
        "detail": interop_detail,
    }


def build_isolated_env(
    *,
    base_env: dict[str, str],
    isolated_home: Path,
    cache_dir: Path,
) -> dict[str, str]:
    env = dict(base_env)
    writable_cache = resolve_writable_cache_dir(cache_dir)
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    (isolated_home / ".local" / "share").mkdir(parents=True, exist_ok=True)
    (isolated_home / ".config").mkdir(parents=True, exist_ok=True)
    for sub in ("uv", "npm", "bun"):
        (writable_cache / sub).mkdir(parents=True, exist_ok=True)

    orig_home = Path(base_env.get("HOME", str(Path.home())))
    extra_paths = [
        str(isolated_home / ".local" / "bin"),
        str(orig_home / ".local" / "bin"),
        str(orig_home / ".bun" / "bin"),
    ]
    bootstrap_paths_file = orig_home / ".local" / "share" / "garden-tools" / "paths"
    if bootstrap_paths_file.is_file():
        for line in bootstrap_paths_file.read_text(encoding="utf-8", errors="replace").splitlines():
            entry = line.strip()
            if entry:
                extra_paths.append(entry)
    current_path = base_env.get("PATH", "")
    path_parts: list[str] = []
    for part in extra_paths + current_path.split(os.pathsep):
        if part and part not in path_parts:
            path_parts.append(part)

    env["HOME"] = str(isolated_home)
    env["XDG_DATA_HOME"] = str(isolated_home / ".local" / "share")
    env["XDG_CONFIG_HOME"] = str(isolated_home / ".config")
    env["GARDEN_HOME"] = str(isolated_home / ".garden")
    env["GARDEN_DSH_HOME"] = str(isolated_home / ".dsh")
    env["GARDEN_BIN_DIR"] = str(isolated_home / ".local" / "bin")
    env["UV_CACHE_DIR"] = str(writable_cache / "uv")
    env["npm_config_cache"] = str(writable_cache / "npm")
    env["BUN_INSTALL_CACHE_DIR"] = str(writable_cache / "bun")
    env["PATH"] = os.pathsep.join(path_parts)
    if "WSL_INTEROP" not in env:
        interop_sock = find_wsl_interop_socket()
        if interop_sock:
            env["WSL_INTEROP"] = interop_sock
    return env


def classify_failure(exit_code: int, log_tail: str) -> str:
    if exit_code in (-9, 137):
        return "infra"
    lowered = log_tail.lower()
    infra_markers = (
        "cannot connect to the docker daemon",
        "is the docker daemon running",
        "no space left on device",
        "cannot allocate memory",
        "killed",
    )
    if any(marker in lowered for marker in infra_markers) and exit_code != 0:
        return "infra"
    return "product"


def git_head_sha(repo_root: Path = REPO_ROOT) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except OSError:
        pass
    return "unknown"


def _cleanup_pass_home(isolated_home: Path, log_file) -> None:
    if not isolated_home.exists():
        return
    try:
        shutil.rmtree(isolated_home)
        log_file.write(f"Removed isolated pass HOME: {isolated_home}\n")
    except OSError as exc:
        log_file.write(f"Warning: could not remove {isolated_home}: {exc}\n")


def _prune_after_run(run_started_wall: float = 0.0) -> None:
    docker_bin = shutil.which("docker")
    if docker_bin:
        for cmd in (
            [docker_bin, "container", "prune", "-f"],
            [docker_bin, "volume", "prune", "-f"],
        ):
            try:
                subprocess.run(cmd, capture_output=True, timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired):
                continue

    tmp_dir = Path("/tmp")
    for prefix in ("garden-dsh-", "garden-demo-"):
        for path in tmp_dir.glob(f"{prefix}*"):
            try:
                if path.stat().st_mtime >= run_started_wall:
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
            except OSError:
                continue


def _run_cleanup(repo_root: Path, env: dict[str, str], log_file) -> dict[str, int]:
    results: dict[str, int] = {}
    commands = {
        "demo-clean": ["just", "demo-clean", "--yes"],
        "demo-uninstall": ["just", "demo-uninstall", "--yes", "--drop-login", "--remove-env"],
    }
    for name, cmd in commands.items():
        log_file.write(f"\n=== cleanup: {' '.join(cmd)} ===\n")
        log_file.flush()
        try:
            proc = subprocess.run(
                cmd,
                cwd=repo_root,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=180,
                check=False,
            )
            results[name] = proc.returncode
        except subprocess.TimeoutExpired:
            log_file.write(f"cleanup {name} timed out\n")
            results[name] = 124
    return results


def execute_demo_pass(
    *,
    name: str,
    kind: str,
    cmd: list[str],
    repo_root: Path,
    output_dir: Path,
    cache_dir: Path,
    persona: str,
    needs_approval: bool,
    approval_timeout: float,
    pass_timeout: float,
    keep_failed: bool,
    interrupt_after_seconds: float | None = None,
) -> PassRecord:
    pass_dir = output_dir / name
    pass_dir.mkdir(parents=True, exist_ok=True)
    isolated_home = pass_dir / "home"
    log_path = pass_dir / "run.log"
    env = build_isolated_env(
        base_env=dict(os.environ), isolated_home=isolated_home, cache_dir=cache_dir
    )

    approval_box: dict[str, object] = {}
    stop_watcher = threading.Event()
    start_wall = time.time() - 1.0
    started = time.monotonic()

    def _watch_and_approve() -> None:
        deadline = time.monotonic() + approval_timeout
        while not stop_watcher.is_set() and time.monotonic() < deadline:
            url = oidc_approve.wait_for_login_url(
                watch_glob="/tmp/garden-demo-*/login-link",
                created_after=start_wall,
                timeout_seconds=1.0,
                poll_interval=0.25,
            )
            if url:
                res = oidc_approve.approve_login_url(url, persona=persona)
                approval_box.update(asdict(res))
                return

    watcher: threading.Thread | None = None
    if needs_approval:
        watcher = threading.Thread(target=_watch_and_approve, daemon=True)
        watcher.start()

    exit_code = 1
    detail = ""
    cleanup_codes: dict[str, int] = {}

    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"=== pass={name} kind={kind} cmd={' '.join(cmd)} ===\n")
        log_file.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=repo_root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            if interrupt_after_seconds is not None:
                try:
                    exit_code = proc.wait(timeout=interrupt_after_seconds)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGINT)
                    try:
                        exit_code = proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        exit_code = proc.wait(timeout=10)
                    detail = (
                        f"Interrupted intentionally after {interrupt_after_seconds:.0f}s "
                        "for recovery test."
                    )
            else:
                try:
                    exit_code = proc.wait(timeout=pass_timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=10)
                    exit_code = 124
                    detail = f"Timed out after {pass_timeout:.0f}s."
        finally:
            stop_watcher.set()
            if watcher is not None:
                watcher.join(timeout=5.0)
            ok_so_far = exit_code == 0 or interrupt_after_seconds is not None
            if ok_so_far or not keep_failed:
                cleanup_codes = _run_cleanup(repo_root, env, log_file)
                _cleanup_pass_home(isolated_home, log_file)

    duration = round(time.monotonic() - started, 2)
    log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]

    approval_ok = not needs_approval or approval_box.get("ok", True)
    if interrupt_after_seconds is not None or (exit_code == 0 and approval_ok):
        ok = True
        failure_category = None
    else:
        ok = False
        failure_category = classify_failure(exit_code, log_tail)
        if not detail:
            if needs_approval and approval_box and not approval_box.get("ok"):
                detail = str(approval_box.get("message", "OIDC approval failed."))
            else:
                detail = f"Command exited with code {exit_code}."

    return PassRecord(
        name=name,
        kind=kind,
        ok=ok,
        exit_code=exit_code,
        failure_category=failure_category,
        duration_seconds=duration,
        log_path=str(log_path),
        approval=approval_box or None,
        cleanup_exit_codes=cleanup_codes,
        detail=detail,
    )


def default_repo_root() -> Path:
    if (REPO_ROOT / "justfile").is_file():
        return REPO_ROOT
    return Path.cwd()


def cmd_preflight(args: argparse.Namespace) -> int:
    report = run_preflight(
        repo_root=args.repo_root,
        min_free_ram_mb=args.min_free_ram_mb,
        min_free_disk_mb=args.min_free_disk_mb,
    )
    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        for name, check in report.checks.items():
            mark = "OK" if check.ok else "FAIL"
            print(f"[{mark}] {name}: {check.detail}")
    return EXIT_OK if report.ok else EXIT_INFRA


def cmd_browser_check(args: argparse.Namespace) -> int:
    result = run_browser_check(timeout=args.timeout)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        mark = "OK" if result["ok"] else "FAIL"
        print(f"[{mark}] {result['detail']}")
    return EXIT_OK if result["ok"] else EXIT_INFRA


def cmd_run(args: argparse.Namespace) -> int:
    run_started_wall = time.time() - 1.0
    repo_root = args.repo_root.resolve()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path(f"/tmp/garden-wsl-accept-{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(),
        "commit": git_head_sha(repo_root),
        "repo_root": str(repo_root),
        "output_dir": str(output_dir),
        "cache_dir": str(args.cache_dir),
        "passes": [],
    }

    if not args.skip_preflight:
        preflight = run_preflight(
            repo_root=repo_root,
            min_free_ram_mb=args.min_free_ram_mb,
            min_free_disk_mb=args.min_free_disk_mb,
        )
        summary["preflight"] = preflight.to_dict()
        if not preflight.ok:
            summary["ok"] = False
            summary["failure_category"] = "infra"
            summary["finished_at"] = datetime.now(UTC).isoformat()
            summary_path = output_dir / "summary.json"
            summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(f"Preflight failed (infra). Summary written to {summary_path}", file=sys.stderr)
            for name, check in preflight.checks.items():
                if not check.ok:
                    print(f"  [FAIL] {name}: {check.detail}", file=sys.stderr)
            return EXIT_INFRA

    records: list[PassRecord] = []
    overall_exit = EXIT_OK

    for index in range(1, args.passes + 1):
        rec = execute_demo_pass(
            name=f"demo-pass-{index}",
            kind="demo",
            cmd=["just", "demo", "--fake-model", "--smoke"],
            repo_root=repo_root,
            output_dir=output_dir,
            cache_dir=args.cache_dir,
            persona=args.persona,
            needs_approval=True,
            approval_timeout=args.approval_timeout,
            pass_timeout=args.pass_timeout,
            keep_failed=args.keep_failed,
        )
        records.append(rec)
        verdict = "PASS" if rec.ok else "FAIL"
        print(f"[{verdict}] {rec.name} ({rec.duration_seconds}s): {rec.detail or 'ok'}")
        if not rec.ok:
            overall_exit = EXIT_INFRA if rec.failure_category == "infra" else EXIT_PRODUCT
            break

    if overall_exit == EXIT_OK and args.demo_web:
        rec = execute_demo_pass(
            name="demo-web",
            kind="demo-web",
            cmd=["just", "demo-web", "--smoke"],
            repo_root=repo_root,
            output_dir=output_dir,
            cache_dir=args.cache_dir,
            persona=args.persona,
            needs_approval=False,
            approval_timeout=args.approval_timeout,
            pass_timeout=args.pass_timeout,
            keep_failed=args.keep_failed,
        )
        records.append(rec)
        verdict = "PASS" if rec.ok else "FAIL"
        print(f"[{verdict}] {rec.name} ({rec.duration_seconds}s): {rec.detail or 'ok'}")
        if not rec.ok:
            overall_exit = EXIT_INFRA if rec.failure_category == "infra" else EXIT_PRODUCT

    if overall_exit == EXIT_OK and args.with_recovery:
        interrupt_rec = execute_demo_pass(
            name="recovery-interrupt",
            kind="recovery-interrupt",
            cmd=["just", "demo", "--fake-model", "--smoke"],
            repo_root=repo_root,
            output_dir=output_dir,
            cache_dir=args.cache_dir,
            persona=args.persona,
            needs_approval=False,
            approval_timeout=args.approval_timeout,
            pass_timeout=args.pass_timeout,
            keep_failed=False,
            interrupt_after_seconds=args.interrupt_after,
        )
        records.append(interrupt_rec)
        print(
            f"[PASS] {interrupt_rec.name} ({interrupt_rec.duration_seconds}s): "
            f"{interrupt_rec.detail}"
        )

        recovery_rec = execute_demo_pass(
            name="recovery-rerun",
            kind="recovery-rerun",
            cmd=["just", "demo", "--fake-model", "--smoke"],
            repo_root=repo_root,
            output_dir=output_dir,
            cache_dir=args.cache_dir,
            persona=args.persona,
            needs_approval=True,
            approval_timeout=args.approval_timeout,
            pass_timeout=args.pass_timeout,
            keep_failed=args.keep_failed,
        )
        records.append(recovery_rec)
        rec_verdict = "PASS" if recovery_rec.ok else "FAIL"
        print(
            f"[{rec_verdict}] {recovery_rec.name} ({recovery_rec.duration_seconds}s): "
            f"{recovery_rec.detail or 'ok'}"
        )
        if not recovery_rec.ok:
            overall_exit = EXIT_INFRA if recovery_rec.failure_category == "infra" else EXIT_PRODUCT

    if overall_exit == EXIT_OK or not args.keep_failed:
        _prune_after_run(run_started_wall)

    summary["passes"] = [asdict(r) for r in records]
    summary["ok"] = overall_exit == EXIT_OK
    summary["failure_category"] = (
        None if overall_exit == EXIT_OK else ("infra" if overall_exit == EXIT_INFRA else "product")
    )
    summary["finished_at"] = datetime.now(UTC).isoformat()
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Summary written to {summary_path}")
    return overall_exit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_pre = sub.add_parser(
        "preflight", help="Check host RAM, disk, Docker, containers, and demo ports."
    )
    p_pre.add_argument("--repo-root", type=Path, default=default_repo_root())
    p_pre.add_argument("--min-free-ram-mb", type=int, default=DEFAULT_MIN_FREE_RAM_MB)
    p_pre.add_argument("--min-free-disk-mb", type=int, default=DEFAULT_MIN_FREE_DISK_MB)
    p_pre.add_argument("--json", action="store_true", dest="as_json")
    p_pre.set_defaults(func=cmd_preflight)

    p_br = sub.add_parser("browser-check", help="Verify WSL-to-Windows browser launcher interop.")
    p_br.add_argument("--timeout", type=float, default=15.0)
    p_br.add_argument("--json", action="store_true", dest="as_json")
    p_br.set_defaults(func=cmd_browser_check)

    p_run = sub.add_parser("run", help="Run isolated WSL demo acceptance passes with warm caches.")
    p_run.add_argument("--repo-root", type=Path, default=default_repo_root())
    p_run.add_argument(
        "--passes", type=int, default=1, help="Number of clean `just demo` passes (default: 1)."
    )
    p_run.add_argument("--demo-web", action="store_true", help="Also run `just demo-web --smoke`.")
    p_run.add_argument(
        "--with-recovery",
        action="store_true",
        help="Run an interrupted-install + uninstall + clean rerun recovery check.",
    )
    p_run.add_argument(
        "--interrupt-after",
        type=float,
        default=25.0,
        help="Seconds before sending SIGINT during recovery-interrupt (default: 25).",
    )
    p_run.add_argument("--persona", default=oidc_approve.DEFAULT_PERSONA)
    p_run.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    p_run.add_argument("--output-dir", type=Path, default=None)
    p_run.add_argument("--keep-failed", action="store_true", help="Skip cleanup when a pass fails.")
    p_run.add_argument("--skip-preflight", action="store_true")
    p_run.add_argument("--min-free-ram-mb", type=int, default=DEFAULT_MIN_FREE_RAM_MB)
    p_run.add_argument("--min-free-disk-mb", type=int, default=DEFAULT_MIN_FREE_DISK_MB)
    p_run.add_argument("--approval-timeout", type=float, default=300.0)
    p_run.add_argument("--pass-timeout", type=float, default=1200.0)
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

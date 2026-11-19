# WSL acceptance testing

This guide defines the standard pipeline for running end-to-end WSL 2 demo acceptance tests on a Windows test machine (`win-test`). It replaces ad-hoc `ssh -> PowerShell -> wsl.exe -> Task Scheduler` scripts and raw Edge CDP WebSocket drivers with direct SSH into WSL 2 and a single checked-in runner.

## Why direct SSH into WSL 2

Running `wsl.exe` through Windows OpenSSH introduces three failure modes:

- PowerShell and `cmd.exe` quoting layers corrupt bash arguments and slow every command.
- When an SSH session ends or drops, WSL can terminate background child processes or stop the distro.
- Working around that with Windows Task Scheduler adds stale `0x41301` task states and unresponsive scheduler queries.

Connecting directly to `sshd` inside the WSL 2 distro (`ssh win-test-wsl`) gives standard Linux `tmux` or `systemd-run --user` detachment, POSIX quoting, and fast round-trips.

## One-time host setup (`win-test-wsl`)

### 1. Keep WSL 2 alive and enable systemd

In Windows (`C:\Users\Administrator\.wslconfig`), keep the WSL 2 VM from idling out mid-run:

```ini
[wsl2]
vmIdleTimeout=-1
```

Inside `Ubuntu-24.04` (`/etc/wsl.conf`), enable `systemd`:

```ini
[boot]
systemd=true
```

Restart WSL once (`wsl.exe --shutdown`) after changing `.wslconfig` or `/etc/wsl.conf`.

If the distro uses `systemd=false` with a custom `[boot] command=` (for example `/usr/local/sbin/docker-wsl-start`), start `/usr/sbin/sshd` inside that boot script instead of enabling `systemd`, and keep one lightweight `wsl.exe -d Ubuntu-24.04 -u root -- sleep infinity` session open so WSL 2 does not idle-stop `sshd`.

Note: SSH sessions into WSL 2 do not always inherit `WSL_INTEROP`. `scripts/wsl_accept.py` automatically discovers the active `/run/WSL/*_interop` socket so Windows browser launchers work inside `ssh win-test-wsl` and `tmux`.

### 2. Enable `sshd` inside `Ubuntu-24.04`

Install and start `openssh-server` on port `2222` (so it does not collide with Windows OpenSSH on port `22`), and authorize your SSH public key in `~/.ssh/authorized_keys`:

```bash
sudo apt-get update && sudo apt-get install -y openssh-server tmux
sudo mkdir -p /etc/ssh/sshd_config.d
printf 'Port 2222\nPasswordAuthentication no\nPubkeyAuthentication yes\n' \
  | sudo tee /etc/ssh/sshd_config.d/99-wsl-test.conf
sudo systemctl enable --now ssh
```

### 3. Connect via a persistent local SSH tunnel (recommended) or `ProxyJump`

A single persistent local port-forward tunnel over Windows OpenSSH port `22` avoids Windows Firewall rules, `netsh portproxy`, and per-command `ProxyJump` handshakes, keeping `ssh win-test-wsl` round-trips under a second:

```bash
# Find the WSL 2 IP once:
wsl_ip=$(ssh win-test "wsl.exe -d Ubuntu-24.04 -u root -- hostname -I" | awk '{print $1}')

# Keep WSL 2 alive and forward 127.0.0.1:22222 -> <wsl_ip>:2222:
setsid -f ssh -n -o ServerAliveInterval=30 -o ServerAliveCountMax=3 win-test \
  "wsl.exe -d Ubuntu-24.04 -u root -- sleep infinity" </dev/null >/tmp/win-test-wsl-keepalive.log 2>&1
setsid -f ssh -n -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  -L "127.0.0.1:22222:${wsl_ip}:2222" win-test </dev/null >/tmp/win-test-wsl-tunnel.log 2>&1
```

Then add `win-test-wsl` to `~/.ssh/config` on the dev/agent host:

```sshconfig
Host win-test-wsl
  HostName 127.0.0.1
  Port 22222
  User root
  IdentityFile ~/.ssh/id_ed25519
  ServerAliveInterval 15
  ServerAliveCountMax 3
```

(Set `User` to your Ubuntu-24.04 user, for example `dev` or `root`.)

Alternative - If `networkingMode=mirrored` is enabled in `.wslconfig`, port `2222` is reachable directly on the Windows host IP without a tunnel.

### 4. VM baseline recommendations

- **Memory**: 16 GiB RAM is recommended on `win-test` when running Windows + WSL 2 + Docker Desktop + Node/Python builds concurrently. An 8 GiB VM is tight and prone to Windows `Resource-Exhaustion-Detector` OOM kills.
- **Docker**: Docker Desktop with the WSL 2 backend must be running and healthy before starting acceptance runs.

## Running acceptance tests

All acceptance steps live in `scripts/wsl_accept.py` and are exposed through `just wsl-accept`.

### Preflight health check

Run this first to fail fast on infrastructure problems (low RAM, low disk, stopped Docker daemon, leftover `garden-demo` containers, or occupied loopback ports `55000`, `55173`, `55433`, `55082`, `55084`, `55085`):

```bash
just wsl-accept preflight
just wsl-accept preflight --json
```

### Decoupled Windows browser interop check

Verify WSL-to-Windows launcher availability (`cmd.exe` / `powershell.exe` / `explorer.exe`) quickly without coupling it to a full stack install:

```bash
just wsl-accept browser-check
```

### Full acceptance run (detached inside `win-test-wsl`)

Start the run detached in `tmux` so SSH disconnects never kill the build:

```bash
ssh win-test-wsl "tmux new-session -d -s garden-accept 'cd ~/garden && just wsl-accept run --passes 2 --demo-web --with-recovery --output-dir /tmp/garden-accept-latest'"
```

Poll progress with short SSH commands:

```bash
ssh win-test-wsl "tmux has-session -t garden-accept 2>/dev/null && echo RUNNING || echo DONE; tail -n 30 /tmp/garden-accept-latest/*/run.log 2>/dev/null"
```

When finished, read the structured verdict:

```bash
ssh win-test-wsl "cat /tmp/garden-accept-latest/summary.json"
```

### What the runner handles automatically

- **Per-pass isolation with warm caches**: Each pass gets a fresh `HOME`, `GARDEN_HOME`, `GARDEN_BIN_DIR`, and `XDG_*` directory under `--output-dir`, while sharing `UV_CACHE_DIR`, `npm_config_cache`, and `BUN_INSTALL_CACHE_DIR` under `~/.cache/garden-wsl-accept/`.
- **Deterministic fake-OIDC sign-in**: `scripts/oidc_approve.py` watches `/tmp/garden-demo-*/login-link` and completes the seeded `dev.admin@fernhill.example` sign-in over HTTP using Python stdlib only (no Playwright/Chromium `libnspr4` dependency and no PowerShell Edge CDP WebSockets).
- **Guaranteed teardown**: Every pass runs `just demo-clean --yes` and `just demo-uninstall --yes --drop-login --remove-env`, deletes its isolated pass `HOME` directory (keeping `run.log` and `summary.json`), and prunes stopped Docker containers and unused volumes at the end of the run (pass `--keep-failed` only when debugging a failure).
- **Real Windows host disk check**: Inside WSL 2, `preflight` checks `/mnt/c` in addition to `/` and `/tmp` so a full Windows `C:` drive is caught before WSL 2's sparse `ext4.vhdx` hits an I/O error.

## Exit codes and the 2-failure infra circuit breaker

`just wsl-accept` uses distinct exit codes so callers and agents know whether to fix code or escalate an environment issue:

| Exit code | Meaning | Action |
| --- | --- | --- |
| `0` | All requested passes succeeded | Proceed / report green |
| `1` | Product failure (`failure_category: "product"`) | Inspect `run.log` and fix the code |
| `2` | Infrastructure failure (`failure_category: "infra"`: preflight fail, OOM/SIGKILL 137, Docker daemon lost, disk full) | Do not chase as a product bug |

**Circuit breaker rule for coding agents**: If remote acceptance fails **twice in a row** with exit code `2` (or SSH/WSL connectivity/timeout errors) rather than a reproducible product failure, **stop immediately and report the infrastructure blocker** instead of retrying or writing ad-hoc `/tmp` workarounds.

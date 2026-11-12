#!/usr/bin/env bash
# Install the garden CLI, dsh and its skills on a laptop, from the portal alone.
#
# Usage: GARDEN_API_URL=https://portal.example install/install.sh
# or, as a one-liner: curl -fsSL https://portal.example/api/agent/downloads/install.sh | GARDEN_API_URL=https://portal.example bash
# Needs curl. The installer gets Python 3.12 itself, with the pinned uv release, whose SHA-256 it
# checks. A pinned Node.js runtime is installed when Node is missing or older than 24. Nothing
# is installed unless the portal's SHA-256 matches.
#
# Environment:
#   GARDEN_API_URL       the portal address (required)
#   GARDEN_PYTHON           a Python 3.12 interpreter to run garden with, instead of the one the
#                          installer fetches under GARDEN_HOME (default: that pinned Python 3.12)
#   GARDEN_BIN_DIR          where the `garden` and `dsh` launchers go (default: ~/.local/bin)
#   GARDEN_HOME             garden's own folder, where dsh is unpacked too (default: ~/.garden)
#   GARDEN_DSH_HOME         dsh's home, where cordis.patch.yml goes (default: ~/.dsh)
#   GARDEN_INSTALL_TEST     set to 1 for an unattended run. The login link goes to GARDEN_LOGIN_LINK_FILE
#                          when that is set, so a browser can approve it; otherwise it is opened with
#                          curl. Used by agent/tests/test_install.py.
#   GARDEN_LOGIN_LINK_FILE  with GARDEN_INSTALL_TEST=1: the file the login link is written to
#
# The model is the portal's: no model setting, endpoint or key is taken or written here.
# dsh's model call uses the person's CLI login, which `garden dsh` reads when it starts.
set -eu

portal="${GARDEN_API_URL:?Set GARDEN_API_URL to the portal address, such as https://portal.example}"
portal="${portal%/}"
bin_dir="${GARDEN_BIN_DIR:-$HOME/.local/bin}"
garden_home="${GARDEN_HOME:-$HOME/.garden}"
dsh_home="${GARDEN_DSH_HOME:-$HOME/.dsh}"
stamp="$(date +%Y%m%d%H%M%S)"
uv_version="0.13.1"
manifest_path="$HOME/.local/share/garden/install-manifest.json"
garden_home_existed=0
bin_dir_existed=0
dsh_home_existed=0
python_home_existed=0
uv_dir_existed=0
pyz_existed=0
config_existed=0
garden_launcher_existed=0
dsh_launcher_existed=0
dsh_config_existed=0
skills_existed=0
login_succeeded=0
[ -e "$garden_home" ] && garden_home_existed=1
[ -e "$bin_dir" ] && bin_dir_existed=1
[ -e "$dsh_home" ] && dsh_home_existed=1
[ -e "$garden_home/python" ] && python_home_existed=1
[ -e "$garden_home/tools/uv-$uv_version" ] && uv_dir_existed=1
[ -e "$garden_home/garden.pyz" ] && pyz_existed=1
[ -e "$garden_home/config.json" ] && config_existed=1
[ -e "$bin_dir/garden" ] && garden_launcher_existed=1
[ -e "$bin_dir/dsh" ] && dsh_launcher_existed=1
[ -e "$garden_home/dsh-config.yml" ] && dsh_config_existed=1
[ -e "$garden_home/skills" ] && skills_existed=1

command -v curl >/dev/null 2>&1 || { echo "curl is needed. Install it, then run the installer again." >&2; exit 1; }

# Progress: every stage prints one numbered line, [n/total], then a Done or Failed line, all on stderr.
# The total is worked out before the first stage (see below). Nothing here changes what is installed.
step=0
total_steps=0
current_step=""
begin_step() {
    step=$((step + 1))
    current_step="$1"
    printf '[%s/%s] %s\n' "$step" "$total_steps" "$current_step" >&2
}
finish_step() {
    printf '[%s/%s] Done: %s\n' "$step" "$total_steps" "$current_step" >&2
    current_step=""
}
# Ends the install at the current stage, with a message. Nothing after it runs.
fail_step() {
    printf '[%s/%s] Failed: %s\n' "$step" "$total_steps" "$current_step" >&2
    current_step=""
    echo "$1" >&2
    exit 1
}
# Any other failing command also ends the stage it was in. The EXIT trap reports it once: a subshell
# ($(...)) does not run the trap, so a failure inside $(...) is reported by the main shell only.
# The trap is EXIT rather than ERR, because dash (the sh that `curl ... | sh` runs) has no ERR trap.
on_exit() {
    local code=$?
    if [ "$code" -ne 0 ] && [ -n "$current_step" ]; then
        printf '[%s/%s] Failed: %s\n' "$step" "$total_steps" "$current_step" >&2
    fi
    write_install_manifest || true
    cleanup
    exit "$code"
}

write_install_manifest() {
    local interpreter="${python:-}"
    if [ -z "$interpreter" ] || [ ! -x "$interpreter" ]; then
        interpreter="$(command -v python3 || true)"
    fi
    [ -n "$interpreter" ] || return 0
    local record_tree
    record_tree() {
        local path="$1" existed="$2" root="$3"
        if [ "$existed" = 0 ] && [ -d "$path" ]; then
            manifest_update artifact tree "$path" "$root"
        fi
    }
    record_directory() {
        local path="$1" existed="$2" root="$3"
        if [ "$existed" = 0 ] && [ -d "$path" ]; then
            manifest_update artifact directory "$path" "$root"
        fi
    }
    record_file() {
        local path="$1" existed="$2" root="$3"
        if [ "$existed" = 0 ] && [ -f "$path" ]; then
            manifest_update artifact file "$path" "$root"
        fi
    }
    record_tree "$garden_home" "$garden_home_existed" "$(dirname "$garden_home")"
    record_directory "$bin_dir" "$bin_dir_existed" "$(dirname "$bin_dir")"
    record_file "$bin_dir/garden" "$garden_launcher_existed" "$bin_dir"
    record_file "$bin_dir/dsh" "$dsh_launcher_existed" "$bin_dir"
    record_tree "$dsh_home" "$dsh_home_existed" "$(dirname "$dsh_home")"
    if [ "${dsh_patch_written:-0}" = 1 ]; then
        manifest_update artifact file "$dsh_home/cordis.patch.yml" "$dsh_home"
    fi
    if [ -n "${dsh_patch_backup:-}" ] && [ -f "$dsh_patch_backup" ]; then
        manifest_update artifact file "$dsh_patch_backup" "$dsh_home"
    fi
    record_tree "$garden_home/python" "$python_home_existed" "$garden_home"
    record_tree "$garden_home/tools/uv-$uv_version" "$uv_dir_existed" "$garden_home"
    if [ -n "${node_tree:-}" ]; then
        record_tree "$node_tree" "$node_tree_existed" "$garden_home/tools"
    fi
    if [ "$garden_home_existed" = 1 ]; then
        record_file "$garden_home/garden.pyz" "$pyz_existed" "$garden_home"
        record_file "$garden_home/config.json" "$config_existed" "$garden_home"
        record_file "$garden_home/dsh-config.yml" "$dsh_config_existed" "$garden_home"
        record_tree "$garden_home/skills" "$skills_existed" "$garden_home"
        if [ -d "$garden_home/dsh" ] && [ "${dsh_replaced:-0}" = 1 ]; then
            manifest_update artifact tree "$garden_home/dsh" "$garden_home"
        fi
    fi
    if [ "$login_succeeded" = 1 ]; then
        manifest_update login "$portal" "$bin_dir/garden" "$garden_home"
    fi
}

manifest_update() {
    "$interpreter" - "$manifest_path" "$@" <<'PY'
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

manifest_path = Path(sys.argv[1])
action, *values = sys.argv[2:]
manifest_path.parent.mkdir(parents=True, exist_ok=True)
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for prop in ("artifacts", "path_entries", "demo_projects", "docker_images", "logins"):
        manifest.setdefault(prop, [])
else:
    manifest = {"version": 1, "artifacts": [], "path_entries": [], "demo_projects": [], "docker_images": [], "logins": []}
if action == "artifact":
    kind, value, root = values
    path = Path(value).absolute()
    entry = {"path": str(path), "resolved_path": str(path.resolve(strict=False)), "root": str(Path(root).absolute().resolve(strict=False)), "kind": kind, "owner": "agent-installer"}
    if kind == "file":
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    found = next((item for item in manifest["artifacts"] if os.path.normcase(item["path"]) == os.path.normcase(str(path))), None)
    if found and found.get("kind") == "tree":
        entry["kind"] = "tree"
        entry["root"] = found.get("root", entry["root"])
    if found:
        manifest["artifacts"][manifest["artifacts"].index(found)] = entry
    else:
        manifest["artifacts"].append(entry)
elif action == "login":
    portal, launcher, garden_home = values
    found = next((item for item in manifest["logins"] if item["portal"] == portal), None)
    entry = {"portal": portal, "launcher": launcher, "garden_home": garden_home}
    if found:
        manifest["logins"][manifest["logins"].index(found)] = entry
    else:
        manifest["logins"].append(entry)
temporary = manifest_path.with_suffix(".tmp")
temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, manifest_path)
os.chmod(manifest_path, 0o600)
PY
}

format_size() {
    awk -v bytes="$1" 'BEGIN { if (bytes >= 1048576) printf "%.1f MB", bytes / 1048576; else if (bytes >= 1024) printf "%.0f KB", bytes / 1024; else printf "%d bytes", bytes }'
}

# Starts a download stage. On a terminal curl draws its own meter, with the bytes received and the
# total; otherwise it stays quiet and the stage lines are all that is printed. The caller finishes the
# stage, after it has checked the file.
download() {
    local url="$1" destination="$2" label="$3" size="${4:-}"
    local description="$label"
    if [ -n "$size" ]; then description="$label ($(format_size "$size"))"; fi
    begin_step "Downloading $description..."
    if [ -t 2 ]; then
        curl --fail --show-error --location --output "$destination" "$url"
    else
        curl --fail --silent --show-error --location --output "$destination" "$url"
    fi
}

tmp="$(mktemp -d)"
# Recycle downloads when trash is available; otherwise leave working material in system temp.
cleanup() {
    if command -v trash >/dev/null 2>&1; then trash "$tmp"; fi
}
trap on_exit EXIT

# uv and the Python it installs are pinned here. Each uv release tarball is checked against the
# SHA-256 that its release publishes, so a changed download is refused before anything runs.
python_version="3.12.15"
uv_dir="$garden_home/tools/uv-$uv_version"
uv="$uv_dir/uv"

uv_target() {
    case "$(uname -s)/$(uname -m)" in
        Linux/x86_64) echo x86_64-unknown-linux-gnu ;;
        Linux/aarch64 | Linux/arm64) echo aarch64-unknown-linux-gnu ;;
        Darwin/x86_64) echo x86_64-apple-darwin ;;
        Darwin/arm64) echo aarch64-apple-darwin ;;
        *) echo "There is no uv build for $(uname -s) $(uname -m), so this installer cannot run here." >&2; exit 1 ;;
    esac
}

uv_sha256() {
    case "$1" in
        x86_64-unknown-linux-gnu) echo eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220 ;;
        aarch64-unknown-linux-gnu) echo 9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14 ;;
        x86_64-apple-darwin) echo bb7b805bdcf3d0bc8c74ceb94569e6b3e8aa35bd492acdbb9d46bf46d9d4ff6b ;;
        aarch64-apple-darwin) echo cfe454573412ff7380d15fc7dae5997ab53ffce72fce0a78e91ddbc84001acd2 ;;
    esac
}

file_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

# Prints the path of a Python 3.12 that uv installs under GARDEN_HOME. GARDEN_PYTHON, when it is set,
# is used as it is and checked below.
install_python() {
    if [ -n "${GARDEN_PYTHON:-}" ]; then
        python="$GARDEN_PYTHON"
        return
    fi
    local target tarball
    target="$(uv_target)"
    mkdir -p "$garden_home"
    if [ ! -x "$uv" ]; then
        tarball="$tmp/uv.tar.gz"
        download "https://github.com/astral-sh/uv/releases/download/$uv_version/uv-$target.tar.gz" \
            "$tarball" "uv $uv_version"
        if [ "$(file_sha256 "$tarball")" != "$(uv_sha256 "$target")" ]; then
            fail_step "uv does not match its published SHA-256. Nothing was installed."
        fi
        finish_step
        begin_step "Unpacking uv $uv_version..."
        mkdir -p "$uv_dir"
        tar -xzf "$tarball" -C "$uv_dir" --strip-components=1 "uv-$target/uv"
        finish_step
    fi
    begin_step "Installing Python $python_version..."
    # Progress goes to stderr, so the path is the only thing on stdout. --no-bin keeps uv from
    # writing a python shim into the launcher folder, where uv would warn about an existing one.
    UV_PYTHON_INSTALL_DIR="$garden_home/python" "$uv" python install --no-bin --quiet "$python_version" >&2
    python="$(UV_PYTHON_INSTALL_DIR="$garden_home/python" "$uv" python find --managed-python "$python_version")"
    finish_step
}

# The stages this run takes, for the [n/total] prefix: eight common ones, plus the Python stages when
# GARDEN_PYTHON is unset (two more of them when uv has to be fetched). Keep this count in step with
# begin_step; the installer test checks that the last stage is the total.
total_steps=8
if [ -z "${GARDEN_PYTHON:-}" ]; then
    total_steps=$((total_steps + 1))
    if [ ! -x "$uv" ]; then total_steps=$((total_steps + 2)); fi
fi
# Keep Node under Garden's home, and prepend it in the launcher for future shells too.
node_bin=""
node_tree=""
node_tree_existed=1
if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)' 2>/dev/null; then
    case "$(uname -s)/$(uname -m)" in
        Linux/x86_64) node_asset=node-v26.13.0-linux-x64; node_sha=5848553868b402d3fabb5b09270649977780a390f832155721025a08b5104455 ;;
        Linux/aarch64 | Linux/arm64) node_asset=node-v26.13.0-linux-arm64; node_sha=439e9c438acdf6a6ffe47418da709153d66c4bed812e30a473faacb107dd2791 ;;
        Darwin/x86_64) node_asset=node-v26.13.0-darwin-x64; node_sha=525c9b29bb0263d687de60a4cec0e2a6db876a8eb032369e5795eca71e7c358e ;;
        Darwin/arm64) node_asset=node-v26.13.0-darwin-arm64; node_sha=d38bd5e5a2ddce6bbb5ce45e46e208337da4d51fffe611e567fd32073d6439f6 ;;
        *) echo "No pinned Node runtime for this platform." >&2; exit 1 ;;
    esac
    node_bin="$garden_home/tools/$node_asset/bin"
    node_tree="$garden_home/tools/$node_asset"
    [ -e "$node_tree" ] || node_tree_existed=0
    if [ ! -x "$node_bin/node" ]; then
        total_steps=$((total_steps + 2))
    fi
fi

install_python
command -v "$python" >/dev/null 2>&1 || { echo "Python 3.12 could not be found at $python. Set GARDEN_PYTHON, or run the installer again." >&2; exit 1; }
"$python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
    || { echo "Python 3.12 or newer is needed; $python is older." >&2; exit 1; }

if [ -n "$node_bin" ]; then
    if [ ! -x "$node_bin/node" ]; then
        download "https://nodejs.org/dist/v26.13.0/$node_asset.tar.gz" "$tmp/node.tar.gz" "Node.js 26.13.0"
        [ "$(file_sha256 "$tmp/node.tar.gz")" = "$node_sha" ] || fail_step "Node.js checksum mismatch."
        finish_step
        begin_step "Unpacking Node.js..."
        mkdir -p "$garden_home/tools"
        tar -xzf "$tmp/node.tar.gz" -C "$garden_home/tools"
        finish_step
    fi
    export PATH="$node_bin:$PATH"
    "$node_bin/node" -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)'
fi

# The manifest lists each artifact with its URL and SHA-256. It needs no sign-in.
begin_step "Fetching download manifest..."
curl --fail --silent --show-error --location --output "$tmp/manifest.json" "$portal/api/agent/manifest"
finish_step

artifact_field() {
    "$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifacts"][sys.argv[2]][sys.argv[3]])' \
        "$tmp/manifest.json" "$1" "$2"
}

# Download one artifact into $tmp/<name> and check it against the manifest. Nothing is installed here.
fetch_artifact() {
    local name="$1" url sha actual size
    url="$portal$(artifact_field "$name" url)"
    sha="$(artifact_field "$name" sha256)"
    size="$(artifact_field "$name" size_bytes)"
    download "$url" "$tmp/$name" "$name" "$size"
    actual="$("$python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$tmp/$name")"
    if [ "$actual" != "$sha" ]; then
        fail_step "$name does not match the portal's SHA-256. Nothing was installed."
    fi
    finish_step
}

mkdir -p "$garden_home" "$bin_dir" "$dsh_home"

fetch_artifact garden.pyz
begin_step "Installing garden CLI..."
install -m 0644 "$tmp/garden.pyz" "$garden_home/garden.pyz"
finish_step

# The launcher runs the zipapp with the interpreter checked above.
cat > "$bin_dir/garden" <<LAUNCHER
#!/usr/bin/env bash
export PATH="${node_bin:+$node_bin:}\$PATH"
exec "$python" "$garden_home/garden.pyz" "\$@"
LAUNCHER
chmod 0755 "$bin_dir/garden"

# config.json holds where the portal is and where dsh lives. It holds no secret.
"$python" -c 'import json,sys; json.dump({"portal_url": sys.argv[1], "garden_command": sys.argv[2], "dsh_home": sys.argv[3]}, sys.stdout)' \
    "$portal" "$bin_dir/garden" "$dsh_home" > "$garden_home/config.json"

export GARDEN_HOME="$garden_home" GARDEN_API_URL="$portal"

# Opens a link in the browser without printing anything. A machine with no opener is not an error.
open_link() {
    if command -v open >/dev/null 2>&1; then
        open "$1" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$1" >/dev/null 2>&1 || true
    fi
}

# Signs the person in with the CLI. Its output is the sign-in link with one line on what to do, not
# the CLI's own messages. The test mode writes the link to a file for a browser to approve, or opens
# it with curl. Returns non-zero when the sign-in does not finish.
sign_in() {
    local log link login_pid
    log="$tmp/login.log"
    "$bin_dir/garden" login --no-open > "$log" 2>&1 &
    login_pid=$!
    link=""
    for _ in $(seq 1 100); do
        link="$(sed -n 's/^  //p' "$log" | head -n 1)"
        [ -n "$link" ] && break
        sleep 0.1
    done
    if [ -z "$link" ]; then
        kill "$login_pid" 2>/dev/null || true
        return 1
    fi
    if [ -n "${GARDEN_LOGIN_LINK_FILE:-}" ]; then
        printf '%s\n' "$link" > "$GARDEN_LOGIN_LINK_FILE"
        echo "The login link is in $GARDEN_LOGIN_LINK_FILE. Open it to approve the login."
    else
        echo "Approve the sign-in in your browser: $link"
        if [ -n "${GARDEN_INSTALL_TEST:-}" ]; then
            curl --fail --silent --location --output /dev/null "$link"
        else
            open_link "$link"
        fi
    fi
    wait "$login_pid"
}

begin_step "Installing garden skills..."
"$bin_dir/garden" skills install --to "$garden_home/skills" >/dev/null
finish_step

# dsh is unpacked from the portal's bundle, which carries its own node_modules. The bundle is a
# fresh copy each time, so an older dsh never mixes with a newer one.
fetch_artifact dsh-bundle.tar.gz
begin_step "Unpacking dsh..."
if [ -e "$garden_home/dsh" ]; then
    if [ -L "$garden_home/dsh" ] || ! "$python" - "$manifest_path" "$garden_home/dsh" <<'PY'
import json
import os
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")) if Path(sys.argv[1]).exists() else {}
target = Path(os.path.normcase(os.path.abspath(sys.argv[2])))
for item in manifest.get("artifacts", []):
    recorded = Path(os.path.normcase(os.path.abspath(item["path"])))
    if target == recorded:
        raise SystemExit(0)
    if item.get("kind") == "tree":
        try:
            target.relative_to(recorded)
        except ValueError:
            continue
        raise SystemExit(0)
raise SystemExit(1)
PY
    then
        echo "Refusing to replace the existing unrecorded dsh folder at $garden_home/dsh." >&2
        exit 1
    fi
    backup="$garden_home/dsh.bak-$stamp-$$"
    mv "$garden_home/dsh" "$backup"
    manifest_update artifact tree "$backup" "$garden_home"
fi
mkdir -p "$garden_home/dsh"
dsh_replaced=1
tar -xzf "$tmp/dsh-bundle.tar.gz" -C "$garden_home/dsh"
finish_step

# dsh's launcher is written by garden: it runs `garden dsh`, which reads the login at run time.
"$bin_dir/garden" dsh-launcher --to "$bin_dir/dsh" --garden "$bin_dir/garden"

# dsh reads its home patch on start. An existing file is kept as a dated backup.
if [ -e "$dsh_home/cordis.patch.yml" ]; then
    dsh_patch_backup="$dsh_home/cordis.patch.yml.bak-$stamp"
    mv "$dsh_home/cordis.patch.yml" "$dsh_patch_backup"
fi
fetch_artifact dsh-config.yml
begin_step "Installing dsh configuration template..."
install -m 0644 "$tmp/dsh-config.yml" "$garden_home/dsh-config.yml"
finish_step

# Sign in last: garden, dsh and the skills are installed before it, so a failed sign-in
# leaves them in place, and the message names the one command to retry.
if ! sign_in; then
    echo "Sign-in did not finish. Everything else is installed. Run garden login to sign in." >&2
    exit 1
fi
login_succeeded=1

# The patch names the portal's default model, which needs the sign-in, so garden writes it now.
"$bin_dir/garden" dsh-config >/dev/null
dsh_patch_written=1

# A smoke check: the signed-in person can list their tools.
tool_count="$("$bin_dir/garden" tools --json | "$python" -c 'import json,sys; print(len(json.load(sys.stdin)))')"
if [ "$tool_count" -gt 0 ]; then
    echo "Ready: $tool_count tools are available. Run \`garden tools\` to see them."
else
    echo "No tools are listed for you yet. Ask the person who runs Garden to give you a permission, then run \`garden tools\` again." >&2
fi

echo "garden is installed at $bin_dir/garden. Skills are in $garden_home/skills."
echo "dsh is installed at $bin_dir/dsh. It uses the portal's model with your sign-in."
if command -v node >/dev/null 2>&1; then
    echo "Run \`dsh\` to start it. Your sign-in is read when it starts, and is never written to dsh's config."
else
    echo "Node.js is not installed. dsh needs it: install Node.js, then run dsh." >&2
fi
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "Add $bin_dir to PATH to run garden and dsh by name." ;;
esac

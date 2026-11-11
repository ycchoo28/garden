#!/usr/bin/env bash
# Source this file so the two-command quick start also updates the current shell's PATH.
garden_bootstrap() (
    set -eu
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        case "$(uname -s)/$(uname -m)" in
            Linux/x86_64) target=x86_64-unknown-linux-gnu; sha=eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220 ;;
            Linux/aarch64|Linux/arm64) target=aarch64-unknown-linux-gnu; sha=9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14 ;;
            Darwin/x86_64) target=x86_64-apple-darwin; sha=bb7b805bdcf3d0bc8c74ceb94569e6b3e8aa35bd492acdbb9d46bf46d9d4ff6b ;;
            Darwin/arm64) target=aarch64-apple-darwin; sha=cfe454573412ff7380d15fc7dae5997ab53ffce72fce0a78e91ddbc84001acd2 ;;
            *) echo 'Unsupported platform for bootstrap.' >&2; exit 1 ;;
        esac
        staging="$(mktemp -d)"
        curl -fSL "https://github.com/astral-sh/uv/releases/download/0.13.1/uv-$target.tar.gz" -o "$staging/uv.tar.gz"
        if command -v sha256sum >/dev/null 2>&1; then
            actual="$(sha256sum "$staging/uv.tar.gz" | cut -d' ' -f1)"
        else
            actual="$(shasum -a 256 "$staging/uv.tar.gz" | cut -d' ' -f1)"
        fi
        [ "$actual" = "$sha" ] || { echo 'uv checksum mismatch.' >&2; exit 1; }
        tar -xzf "$staging/uv.tar.gz" -C "$staging"
        mkdir -p "$HOME/.local/bin"
        cp "$staging/uv-$target/uv" "$HOME/.local/bin/uv"
    fi
    uv run --no-project --python 3.12 "$(dirname "${BASH_SOURCE[0]}")/bootstrap.py"
)
if garden_bootstrap; then
    export PATH="$HOME/.local/bin:$PATH"
    if [ -f "$HOME/.local/share/garden-tools/paths" ]; then
        while IFS= read -r garden_tool_path; do export PATH="$garden_tool_path:$PATH"; done < "$HOME/.local/share/garden-tools/paths"
        unset garden_tool_path
    fi
else
    return 1 2>/dev/null || exit 1
fi

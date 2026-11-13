# Local commands for Garden. Run `just` with no arguments to list them.
set dotenv-load := true
set positional-arguments := true

# Fixed local ports, all on 127.0.0.1. Postgres and the fake sign-in provider are in dev/compose.yaml.
api_url := "http://127.0.0.1:55000"
demo_just_dotenv_path := env_var_or_default("GARDEN_DEMO_JUST_DOTENV_PATH", ".env")
web_url := "http://127.0.0.1:55173"
# Windows runs recipe lines through scripts/just_shell.py, which uses Git for Windows bash and never
# the WSL bash.exe on PATH. Linux and macOS ignore this setting. Older just versions read it too.
set windows-shell := ["uv", "run", "--no-project", "--python", "3.12", "python", "scripts/just_shell.py", "-cu"]

default:
    @just --list

# Create .env from the example if it is missing, then install backend, agent and frontend dependencies.
[unix]
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$PWD"
    uv run --no-project --python 3.12 python scripts/demo.py --record-checkout-baseline
    trap 'uv run --no-project --python 3.12 python "$root/scripts/demo.py" --record-checkout-dependencies' EXIT
    [ -f .env ] || cp .env.example .env
    cd backend && uv sync
    cd ../agent && uv sync
    cd ../frontend && bun install --frozen-lockfile

# Start Postgres and the fake sign-in provider in Docker Compose and wait until they are healthy.
[unix]
up:
    #!/usr/bin/env bash
    set -euo pipefail
    source infra/postgres/versions.env
    docker build -f infra/postgres/Dockerfile \
        --build-arg "POSTGRES_BASE_IMAGE=$POSTGRES_BASE_IMAGE" \
        --build-arg "PGVECTOR_APT_VERSION=$PGVECTOR_APT_VERSION" \
        -t garden-postgres:local infra/postgres
    GARDEN_DEV_POSTGRES_IMAGE=garden-postgres:local docker compose -f dev/compose.yaml up -d --wait

# Download the pinned bge-m3 model into var/models and check its SHA-256. A file that does not match is moved aside, never used.
[unix]
models:
    #!/usr/bin/env bash
    set -euo pipefail
    source infra/embedding/model.env
    mkdir -p var/models
    target="var/models/$MODEL_FILE"
    if [ -f "$target" ] && [ "$(shasum -a 256 "$target" | cut -d' ' -f1)" = "$MODEL_SHA256" ]; then
        echo "The model is present and matches its pinned SHA-256."
        exit 0
    fi
    partial="$target.partial"
    curl --fail --location --output "$partial" "https://huggingface.co/$MODEL_REPO/resolve/$MODEL_REVISION/$MODEL_FILE"
    actual="$(shasum -a 256 "$partial" | cut -d' ' -f1)"
    if [ "$actual" != "$MODEL_SHA256" ]; then
        mv -f "$partial" "$target.mismatch"
        echo "The downloaded model does not match its pinned SHA-256. It was moved to $target.mismatch." >&2
        exit 1
    fi
    mv -f "$partial" "$target"

# Start the local embedding service (llama.cpp serving bge-m3) in Compose. Not part of `just up`.
embedding-up: models
    docker compose -f dev/compose.yaml --profile embedding up -d --wait embedding

# Apply Alembic migrations, and create the job queue tables if they are missing.
[unix]
migrate: up
    #!/usr/bin/env bash
    set -euo pipefail
    cd backend
    # Migrates the database in GARDEN_DATABASE_URL and adds the job queue tables when missing.
    uv run python -m app.migrate

# Load the fake organisation and the seeded personas. Idempotent. Migrates first.
seed: migrate
    cd backend && uv run python -m app.seed

# Run the API with reload, the background worker and the Vite dev server until Ctrl+C. Seeds first.
[unix]
dev: seed
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    (cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 55000) &
    (cd backend && uv run python -m app.worker) &
    (cd frontend && bun run dev --strictPort --port 55173) &
    wait

# Full agent demo: configure the model, publish/install garden and dsh, sign in, and run the portal.
[unix]
demo *args:
    #!/usr/bin/env bash
    set -euo pipefail
    GARDEN_DEMO_JUST_DOTENV_PATH="{{demo_just_dotenv_path}}" uv run --no-project --python 3.12 python scripts/demo.py --check-docker "$@"
    GARDEN_DEMO_JUST_DOTENV_PATH="{{demo_just_dotenv_path}}" uv run --project backend python scripts/demo.py "$@"

# Web-only demo: initialize .env/dependencies, migrate, seed and run the portal without an agent install.
[unix]
demo-web *args:
    #!/usr/bin/env bash
    set -euo pipefail
    GARDEN_DEMO_JUST_DOTENV_PATH="{{demo_just_dotenv_path}}" uv run --no-project --python 3.12 python scripts/demo.py --check-docker --web-only "$@"
    GARDEN_DEMO_JUST_DOTENV_PATH="{{demo_just_dotenv_path}}" uv run --project backend python scripts/demo.py --web-only "$@"

# Stop this clone's demo processes and remove only its Compose containers, networks and volumes.
[unix]
demo-clean *args:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run --no-project --python 3.12 python scripts/demo.py --clean "$@"

# Revoke the Garden CLI login, then remove this clone's recorded Garden installation and demo artifacts.
[unix]
demo-uninstall *args:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run --no-project --python 3.12 python scripts/demo.py --uninstall "$@"

# Exercise both demo commands with fresh test databases, ports, environment files and homes.
demo-e2e: up
    GARDEN_DEMO_E2E=1 uv run --project backend pytest scripts/tests/test_demo_e2e.py -q

# Drop and recreate the dev database named in .env, then migrate and load the demo data again. Asks for confirmation first.
[unix]
reset: up
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill "${GARDEN_FAKE_EMBEDDER_PID:-}" 2>/dev/null || true' EXIT
    read -r -p "This drops the dev database in .env and loads the demo data again. Type 'reset' to continue: " answer
    [ "$answer" = "reset" ] || { echo "aborted"; exit 1; }
    db="${GARDEN_DATABASE_URL##*/}"
    if [[ ! "$db" =~ ^[A-Za-z0-9_]+$ ]] || [[ "$db" == *_test ]]; then
        echo "Refusing to reset '$db'. Only the dev database in .env is reset, and it must not be a test database." >&2
        exit 1
    fi
    docker compose -f dev/compose.yaml exec -T postgres psql -U garden -d postgres -v ON_ERROR_STOP=1 \
        -c "drop database if exists \"$db\" with (force)" \
        -c "create database \"$db\""
    just migrate
    source scripts/demo-embeddings.sh
    garden_select_embeddings
    (cd backend && uv run python -m app.demo_seed)

# The fast checks that CI runs first: backend format and lint, frontend, API contract, secrets, doc links and the deny-list.
quick: backend-check agent-check frontend-check architecture-check api-check secrets-check docs-check deny-check

# Run every local check, quick ones first: then tests against Postgres, browser tests and dependency audits.
check: quick test e2e backend-audit frontend-audit

# Install the pinned lefthook and register the commit hooks in .git/hooks. Run once after cloning.
[unix]
hooks:
    #!/usr/bin/env bash
    set -euo pipefail
    version=2.2.1
    case "$(uname -s)-$(uname -m)" in
        Linux-x86_64) asset=lefthook_2.2.1_Linux_x86_64; sha=ebee376ee803749c39dd8c6fecff47e08e63e03b02b5ef62e3ea616e4312b1e0 ;;
        Linux-aarch64 | Linux-arm64) asset=lefthook_2.2.1_Linux_arm64; sha=c1903087d643cb26187665eaf6f484a8a295af438dad01138f3e54d6b4985b41 ;;
        Darwin-arm64) asset=lefthook_2.2.1_MacOS_arm64; sha=37e5120e2638ac660776b6bf4a6331e504957a8dde53aa3cb57c79dcd0996698 ;;
        Darwin-x86_64) asset=lefthook_2.2.1_MacOS_x86_64; sha=dbff6fba0b69909df0310a6ccd75141c9cb196244b2d89f971ebe93fe96f4801 ;;
        *) echo "No pinned lefthook for $(uname -s) $(uname -m). Install lefthook $version by hand." >&2; exit 1 ;;
    esac
    bin="$HOME/.local/bin"
    mkdir -p "$bin"
    if [ "$(lefthook version 2>/dev/null || true)" != "$version" ]; then
        download="$bin/lefthook.download"
        curl --fail --silent --show-error --location --output "$download" "https://github.com/evilmartians/lefthook/releases/download/v$version/$asset"
        actual="$(shasum -a 256 "$download" | cut -d' ' -f1)"
        [ "$actual" = "$sha" ] || { echo "lefthook $version does not match its pinned SHA-256" >&2; exit 1; }
        chmod 0755 "$download"
        mv -f "$download" "$bin/lefthook"
    fi
    "$bin/lefthook" install

# Python format, lint, strict types and module-boundary rules.
backend-check:
    cd backend && uv run ruff format --check .
    cd backend && uv run ruff check .
    cd backend && uv run pyright
    cd backend && uv run lint-imports
    cd backend && uv run python ../scripts/check_backend_architecture.py
    cd backend && uv run pytest tests/architecture/test_feature_boundaries.py -q

# The garden CLI package: format, lint and strict types.
agent-check:
    cd agent && uv run ruff format --check .
    cd agent && uv run ruff check .
    cd agent && uv run pyright
    cd agent && uv run lint-imports

# Build agent/dist/garden.pyz: the garden CLI with its locked dependencies vendored in, the file the portal serves.
agent-build:
    cd agent && uv run python scripts/build_zipapp.py

# The installer run unattended against the fake portal: the zipapp, login and dsh patch. Needs no network.
agent-install-test:
    cd agent && uv run pytest tests/test_install.py -q

# Build the dsh bundles: agent/dist/dsh-bundle.tar.gz for linux x86_64 (build it on that platform) and
# agent/dist/dsh-bundle-windows-x64.tar.gz for Windows x64. Needs Node.js, npm and network access to the npm registry.
agent-dsh-bundle:
    cd agent && uv run python scripts/build_dsh_bundle.py
    cd agent && uv run python scripts/build_dsh_bundle.py --target windows-x64

# Copy the installer's files into the folder the portal serves: garden.pyz, the dsh bundles, the dsh config template and the installers.
# The dsh bundles take minutes to build, so existing ones are reused; run agent-dsh-bundle to rebuild them.
[unix]
agent-publish: agent-build
    #!/usr/bin/env bash
    set -euo pipefail
    [ -f agent/dist/dsh-bundle.tar.gz ] || (cd agent && uv run python scripts/build_dsh_bundle.py)
    [ -f agent/dist/dsh-bundle-windows-x64.tar.gz ] || (cd agent && uv run python scripts/build_dsh_bundle.py --target windows-x64)
    dest="${GARDEN_AGENT_DOWNLOADS_DIR:-backend/var/agent-downloads}"
    mkdir -p "$dest"
    cp agent/dist/garden.pyz agent/dist/dsh-bundle.tar.gz agent/dist/dsh-bundle-windows-x64.tar.gz "$dest/"
    cp agent/install/dsh/cordis.patch.yml "$dest/dsh-config.yml"
    cp agent/install/install.sh agent/install/install.ps1 "$dest/"
    git rev-parse HEAD > "$dest/build-commit"
    echo "Published to $dest."

# The end-to-end run of the agent contract against its own portal: a fresh *_test database on the dev Postgres,
# the API on 56000 and the web app on 56173 (`just demo` uses 55000 and 55173), a fake model upstream, the installer
# run as `curl ... | sh` with a fresh HOME, and every login and critical action approved in a headless browser.
# The dev database is never used. Writes agent/dist/e2e (the transcript, logs and screenshots).
agent-e2e: up
    cd agent && uv run python scripts/e2e.py

# The same stack as agent-e2e, held up for a hand test from Windows. It prints the installer one-liner and the steps for
# PowerShell, and it stays up until Ctrl+C, which stops its processes and drops its test database. Writes agent/dist/e2e-serve.
agent-e2e-serve: up
    cd agent && uv run python scripts/e2e.py --serve

# Backend tests against the real Postgres in Compose, in a separate *_test database.
# CI runs this against the Postgres that `just up` starts in the same job.
# Set GARDEN_TEST_DB to use another test database (its name must end in _test), for example when worktrees run tests in parallel.
[unix]
test:
    #!/usr/bin/env bash
    set -euo pipefail
    just up
    psql_dev() { docker compose -f dev/compose.yaml exec -T postgres psql -U garden -d garden -v ON_ERROR_STOP=1 "$@"; }
    db="${GARDEN_TEST_DB:-garden_test}"
    if [ "$(psql_dev -tAc "select 1 from pg_database where datname = '$db'")" != "1" ]; then
        psql_dev -c "create database $db"
    fi
    export GARDEN_DATABASE_URL="${GARDEN_DATABASE_URL%/*}/$db"
    cd backend && uv run pytest
    cd ../agent && uv run pytest

# Frontend type check (vue-tsc, strict), lint with module-boundary rules, unit tests, and no raw colours outside the tokens.
frontend-check:
    cd frontend && bun run typecheck
    cd frontend && bun run lint
    cd frontend && bun run test
    scripts/check-ui-colours.sh

# Browser tests: a person signs in through the fake provider and sees the shell. Needs Postgres and the fake provider (see `just up`).
# Playwright starts the API and Vite itself. Chromium must be installed (`bunx playwright install chromium`).
[unix]
e2e: up
    #!/usr/bin/env bash
    set -euo pipefail
    curl --fail --silent --show-error --retry 30 --retry-all-errors --retry-delay 1 "$GARDEN_OIDC_ISSUER/.well-known/openid-configuration" >/dev/null
    # The first sign-in page a fresh provider serves is slow, so warm the page the browser opens too.
    curl --fail --silent --show-error --max-time 60 --retry 30 --retry-all-errors --retry-delay 1 "$GARDEN_OIDC_ISSUER/authorize?client_id=$GARDEN_OIDC_CLIENT_ID&response_type=code&redirect_uri=$GARDEN_PUBLIC_URL/auth/callback&scope=openid&state=warmup" >/dev/null
    # The tests start their own API, worker, web app and embedder, so those ports must be free.
    api="${GARDEN_E2E_API_URL:-{{api_url}}}"
    web="${GARDEN_E2E_WEB_URL:-{{web_url}}}"
    for port in "${api##*:}" "${web##*:}" 55084; do
        if (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
            echo "Port $port is already in use, for example by just demo or just dev. Stop it before the browser tests." >&2
            exit 1
        fi
    done
    # The browser tests get a fresh database of their own, so they never leave rows in the dev database.
    db="${GARDEN_E2E_DB:-garden_e2e}"
    [[ "$db" == *_e2e ]] || { echo "GARDEN_E2E_DB must end in _e2e." >&2; exit 1; }
    docker compose -f dev/compose.yaml exec -T postgres psql -U garden -d garden -v ON_ERROR_STOP=1 \
        -c "drop database if exists \"$db\" with (force)" -c "create database \"$db\""
    export GARDEN_DATABASE_URL="${GARDEN_DATABASE_URL%/*}/$db"
    cd frontend && bun run e2e

# Regenerate the OpenAPI contract and the Hey API client from the backend.
api:
    cd backend && uv run python -m app.export_openapi ../contracts/openapi.json
    cd frontend && bun run generate

# Compare freshly generated API artifacts in temporary storage, leaving the working tree untouched.
api-check:
    uv run --project backend python scripts/check_api.py

# Static architecture checks need no database.
architecture-check:
    uv run --project backend ruff check --config backend/pyproject.toml scripts/check_*architecture.py scripts/check_reporting_contracts.py scripts/check_api.py scripts/check_authoring.py scripts/demo.py scripts/bootstrap.py scripts/tests
    uv run --project backend ruff format --check --config backend/pyproject.toml scripts/check_*architecture.py scripts/check_reporting_contracts.py scripts/check_api.py scripts/check_authoring.py scripts/demo.py scripts/bootstrap.py scripts/tests
    uv run --project backend python scripts/check_shared_architecture.py
    uv run --project backend python scripts/check_frontend_architecture.py
    uv run --project backend python scripts/check_reporting_contracts.py
    uv run --project backend python scripts/check_authoring.py
    cd agent && uv run pytest tests/architecture -q

# Command-level permitted and prohibited examples, also run after Scaffold generation in CI.
guardrail-test:
    uv run --project backend pytest scripts/tests -q

# Scan the git history and the working tree for committed secrets.
secrets-check:
    gitleaks git --no-banner --redact --exit-code 1 .

# Check that the relative links in the Markdown files point at files and headings that exist.
docs-check:
    uv run --no-project python scripts/check_doc_links.py

# Fail when a tracked or unignored file names a term on the deny-list (security/denylist.txt).
deny-check:
    uv run --no-project python scripts/deny_list_check.py

# Audit the locked Python dependencies with pip-audit. Accepted advisories come from security/allowlist.txt.
[unix]
backend-audit:
    #!/usr/bin/env bash
    set -euo pipefail
    cd backend
    mkdir -p var
    requirements="var/audit-requirements.txt"
    uv export --frozen --no-hashes --no-emit-project --format requirements-txt --output-file "$requirements" >/dev/null
    ignores=()
    ids="$(../scripts/allowed-advisories.sh)"
    while read -r id; do
        if [ -n "$id" ]; then ignores+=(--ignore-vuln "$id"); fi
    done <<< "$ids"
    uv run pip-audit --requirement "$requirements" --progress-spinner off ${ignores[@]+"${ignores[@]}"}

# Audit the locked Bun dependencies and fail on high and critical advisories. Accepted advisories come from security/allowlist.txt.
[unix]
frontend-audit:
    #!/usr/bin/env bash
    set -euo pipefail
    ignores=()
    ids="$(scripts/allowed-advisories.sh)"
    while read -r id; do
        if [ -n "$id" ]; then ignores+=(--ignore="$id"); fi
    done <<< "$ids"
    cd frontend && bun audit --audit-level=high ${ignores[@]+"${ignores[@]}"}

# Scaffold a new business module: backend package, schema migration, starter tests and frontend folder.
new-module name:
    uv run --project backend python scripts/new_module.py "{{name}}"

# Windows versions of the demo recipes. Recipes marked [unix] above are not on Windows, so only
# these run there. Lines run one at a time through the shell set above.
[windows]
setup:
    uv run --no-project --python 3.12 python scripts/demo.py --record-checkout-baseline && { [ -f .env ] || cp .env.example .env; } && (cd backend && uv sync) && (cd agent && uv sync) && (cd frontend && bun install --frozen-lockfile); status=$?; uv run --no-project --python 3.12 python scripts/demo.py --record-checkout-dependencies; exit $status

[windows]
demo *args:
    GARDEN_DEMO_JUST_DOTENV_PATH="{{demo_just_dotenv_path}}" uv run --no-project --python 3.12 python scripts/demo.py --check-docker "$@" && GARDEN_DEMO_JUST_DOTENV_PATH="{{demo_just_dotenv_path}}" uv run --project backend python scripts/demo.py "$@"

[windows]
demo-web *args:
    GARDEN_DEMO_JUST_DOTENV_PATH="{{demo_just_dotenv_path}}" uv run --no-project --python 3.12 python scripts/demo.py --check-docker --web-only "$@" && GARDEN_DEMO_JUST_DOTENV_PATH="{{demo_just_dotenv_path}}" uv run --project backend python scripts/demo.py --web-only "$@"

[windows]
demo-clean *args:
    uv run --no-project --python 3.12 python scripts/demo.py --clean "$@"

[windows]
demo-uninstall *args:
    uv run --no-project --python 3.12 python scripts/demo.py --uninstall "$@"

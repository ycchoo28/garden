# Local commands for Garden. Run `just` with no arguments to list them.
set dotenv-load := true

# Fixed local ports, all on 127.0.0.1. Postgres and the fake sign-in provider are in dev/compose.yaml.
api_url := "http://127.0.0.1:55000"
web_url := "http://127.0.0.1:55173"

default:
    @just --list

# Create .env from the example if it is missing, then install backend and frontend dependencies.
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -f .env ] || cp .env.example .env
    cd backend && uv sync
    cd ../frontend && bun install --frozen-lockfile

# Start Postgres and the fake sign-in provider in Docker Compose and wait until they are healthy.
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
migrate: up
    #!/usr/bin/env bash
    set -euo pipefail
    cd backend
    uv run alembic upgrade heads
    queue_exists="$(docker compose -f ../dev/compose.yaml exec -T postgres psql -U garden -d garden -tAc "select to_regclass('public.procrastinate_jobs') is not null")"
    if [ "$queue_exists" = "f" ]; then
        uv run python -m procrastinate --app=app.shared.jobs.app schema --apply
    fi

# Load the fake organisation and the seeded personas. Idempotent. Migrates first.
seed: migrate
    cd backend && uv run python -m app.seed

# Run the API with reload, the background worker and the Vite dev server until Ctrl+C. Seeds first.
dev: seed
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    (cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 55000) &
    (cd backend && uv run python -m app.worker) &
    (cd frontend && bun run dev --strictPort --port 55173) &
    wait

# Delete the local Postgres volume and rebuild the database. Asks for confirmation first.
reset:
    #!/usr/bin/env bash
    set -euo pipefail
    read -r -p "This deletes the local database volume. Type 'reset' to continue: " answer
    [ "$answer" = "reset" ] || { echo "aborted"; exit 1; }
    docker compose -f dev/compose.yaml down -v
    just seed

# The fast checks that CI runs first: backend format and lint, frontend, API contract, secrets, doc links and the deny-list.
quick: backend-check frontend-check api-check secrets-check docs-check deny-check

# Run every local check, quick ones first: then tests against Postgres, browser tests and dependency audits.
check: quick test e2e backend-audit frontend-audit

# Install the pinned lefthook and register the commit hooks in .git/hooks. Run once after cloning.
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

# Backend tests against the real Postgres in Compose, in a separate *_test database.
# CI runs this against the Postgres that `just up` starts in the same job.
# Set GARDEN_TEST_DB to use another test database (its name must end in _test), for example when worktrees run tests in parallel.
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

# Frontend type check (vue-tsc, strict), lint with module-boundary rules, unit tests, and no raw colours outside the tokens.
frontend-check:
    cd frontend && bun run typecheck
    cd frontend && bun run lint
    cd frontend && bun run test
    scripts/check-ui-colours.sh

# Browser tests: a person signs in through the fake provider and sees the shell. Needs Postgres and the fake provider (see `just up`).
# Playwright starts the API and Vite itself. Chromium must be installed (`bunx playwright install chromium`).
e2e: up
    #!/usr/bin/env bash
    set -euo pipefail
    curl --fail --silent --show-error --retry 30 --retry-connrefused --retry-all-errors --retry-delay 1 "$GARDEN_OIDC_ISSUER/.well-known/openid-configuration" >/dev/null
    cd frontend && bun run e2e

# Regenerate the OpenAPI contract and the Hey API client from the backend.
api:
    cd backend && uv run python -m app.export_openapi ../contracts/openapi.json
    cd frontend && bun run generate

# Regenerate the API files and fail if that changes any of them. Compares hashes before and after, so it works before a commit.
api-check:
    #!/usr/bin/env bash
    set -euo pipefail
    snapshot() { find contracts frontend/src/api/generated -type f -print0 | sort -z | xargs -0 sha256sum; }
    before="$(snapshot)"
    just api
    after="$(snapshot)"
    if [ "$before" != "$after" ]; then
        diff <(echo "$before") <(echo "$after") || true
        echo "The API contract or client was stale and has been regenerated. Review the diff, then run just check again." >&2
        exit 1
    fi

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

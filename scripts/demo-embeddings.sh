# Sourced by `just demo` and `just reset`. It sets GARDEN_EMBEDDING_URL for the calling shell.
#
# It uses the real embedding service when that answers. Otherwise it starts the fake embedding server
# that the browser tests use, so no model is downloaded. The fake runs as a background job of the
# calling shell, and GARDEN_FAKE_EMBEDDER_PID holds its pid for the caller to stop.

garden_select_embeddings() {
    local real="${GARDEN_EMBEDDING_URL:-http://127.0.0.1:55083}"
    local fake="http://127.0.0.1:55084"
    if curl --silent --fail --max-time 3 "$real/health" >/dev/null 2>&1; then
        export GARDEN_EMBEDDING_URL="$real"
        echo "Embeddings: the real embedding service at $real."
        return 0
    fi
    if ! curl --silent --fail --max-time 3 "$fake/health" >/dev/null 2>&1; then
        if (echo > /dev/tcp/127.0.0.1/55084) 2>/dev/null; then
            echo "Port 55084 is in use by something that is not the fake embedder." >&2
            return 1
        fi
        (cd backend && exec uv run python -m tests.fake_embedding_server --port 55084) &
        GARDEN_FAKE_EMBEDDER_PID=$!
        export GARDEN_FAKE_EMBEDDER_PID
        for _ in $(seq 1 60); do
            curl --silent --fail --max-time 1 "$fake/health" >/dev/null 2>&1 && break
            sleep 0.5
        done
    fi
    export GARDEN_EMBEDDING_URL="$fake"
    echo "Embeddings: the fake embedding server at $fake, because the real service at $real is not running. No model is downloaded."
}

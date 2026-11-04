"""A fake llama.cpp embeddings server for the browser tests.

It answers `POST /v1/embeddings` with the same deterministic vectors as the backend tests, so the
API and the worker never need the bge-m3 model or an API key. `frontend/playwright.config.ts`
starts it. Run it with `uv run python -m tests.fake_embedding_server --port 55084`.
"""

import argparse
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.fakes import FakeEmbedder


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._send(200, b'{"status":"ok"}')

    def do_POST(self) -> None:
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        vectors = asyncio.run(FakeEmbedder().embed(request["input"]))
        body = {
            "data": [{"index": index, "embedding": vector} for index, vector in enumerate(vectors)]
        }
        self._send(200, json.dumps(body).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the fake embedder on 127.0.0.1.")
    _ = parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", arguments.port), _Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()

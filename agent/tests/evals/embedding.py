"""Select and start the embedding server used by knowledge evals."""

from __future__ import annotations

import hashlib
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tests.shared.paths import REPO
from tests.shared.stack import Services, free_port, wait_for
from tests.shared.transcript import StepFailed


@dataclass(frozen=True)
class EmbeddingServer:
    embedder: str
    url: str


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=3):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _model_hash() -> str:
    model_env = REPO / "infra" / "embedding" / "model.env"
    for line in model_env.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"MODEL_SHA256=([0-9a-f]{64})", line)
        if match is not None:
            return match.group(1)
    raise StepFailed(f"The pinned model hash is missing from {model_env}.")


def ensure_embedding_server(
    env: dict[str, str],
    output: Path,
    services: Services,
    *,
    fake: bool,
) -> EmbeddingServer:
    if fake:
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        fake_env = dict(env)
        fake_env["PYTHONPATH"] = str(REPO / "backend")
        services.start(
            ["uv", "run", "python", "-m", "tests.fake_embedding_server", "--port", str(port)],
            cwd=REPO / "backend",
            env=fake_env,
            log=output / f"fake-embedding-{port}.log",
        )
        wait_for(f"{url}/health", timeout=30)
        source = REPO / "backend" / "tests" / "fakes.py"
        return EmbeddingServer(f"fake:{hashlib.sha256(source.read_bytes()).hexdigest()}", url)

    port = env.get("GARDEN_DEV_EMBEDDING_PORT", "55083")
    url = env.get("GARDEN_EMBEDDING_URL", f"http://127.0.0.1:{port}").rstrip("/")
    if not _healthy(url):
        try:
            result = subprocess.run(
                ["just", "embedding-up"],
                cwd=REPO,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=1800,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StepFailed(
                f"Could not start the embedding server with 'just embedding-up': {error}."
            ) from error
        if result.returncode != 0:
            output_text = (result.stdout + result.stderr).strip()
            raise StepFailed(
                "Could not start the embedding server with 'just embedding-up':\n"
                f"{output_text[-4000:]}"
            )
        wait_for(f"{url}/health", timeout=180)
    return EmbeddingServer(f"bge-m3:{_model_hash()}", url)

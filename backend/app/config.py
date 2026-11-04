"""Typed environment settings, read from GARDEN_* variables or the repository's .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GARDEN_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    environment: Literal["dev", "stage", "prod"] = "dev"
    database_url: str
    # The OpenID Connect issuer that signs people in. Dev uses the fake one in dev/compose.yaml.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    # Points sign-in at the fake OIDC server in dev/compose.yaml. Refused outside dev.
    fake_oidc: bool = False
    # The address the browser uses for this app. Sign-in returns to `{public_url}/auth/callback`.
    public_url: str = "http://localhost:5173"
    # A browser session ends after this many minutes without a request.
    session_idle_minutes: int = 30
    files_root: Path = Path("var/files")
    # The largest file an upload may send, in bytes.
    files_max_bytes: int = 5 * 1024 * 1024
    # The llama.cpp server that serves bge-m3 for embeddings. Started by `just embedding-up`.
    embedding_url: str = "http://127.0.0.1:55083"
    # The model gateway forwards to this OpenAI-compatible base URL. Empty means it answers 503.
    gateway_upstream_url: str = ""
    # Sent to the upstream as a bearer token when set. Not needed by a local or fake upstream.
    gateway_api_key: SecretStr = SecretStr("")
    # The only models the gateway forwards, as a comma-separated list. Empty allows none.
    gateway_models: str = ""
    gateway_requests_per_minute: int = 20
    gateway_tokens_per_day: int = 200_000

    @property
    def allowed_models(self) -> frozenset[str]:
        return frozenset(name.strip() for name in self.gateway_models.split(",") if name.strip())

    @model_validator(mode="after")
    def refuse_fake_oidc_outside_dev(self) -> Self:
        if self.fake_oidc and self.environment != "dev":
            raise ValueError("GARDEN_FAKE_OIDC is only allowed when GARDEN_ENVIRONMENT=dev")
        return self

    @model_validator(mode="after")
    def require_https_outside_dev(self) -> Self:
        if self.environment != "dev" and not self.public_url.startswith("https://"):
            raise ValueError("GARDEN_PUBLIC_URL must use https outside dev")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # database_url comes from the environment

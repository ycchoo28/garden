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
    agent_model_upstream_url: str = ""
    # The hosted MCP endpoint is mounted but answers 404 until an operator enables it.
    agent_mcp_enabled: bool = False
    # The hosted Engine calls the backend through this address with the caller's bearer token.
    agent_mcp_api_base_url: str = "http://127.0.0.1:55000"
    # Comma-separated browser origins allowed to call the hosted MCP endpoint.
    agent_mcp_allowed_origins: str = ""
    agent_mcp_pending_per_login: int = 3
    agent_mcp_pending_total: int = 20
    # Sent to the upstream as a bearer token when set. Not needed by a local or fake upstream.
    agent_model_api_key: SecretStr = SecretStr("")
    # The only models the gateway forwards, as a comma-separated list. Empty allows none.
    agent_model_models: str = ""
    agent_model_requests_per_minute: int = 20
    agent_model_tokens_per_day: int = 200_000
    # The model the gateway names as its `default`, which dsh uses. Empty means the first model in
    # GARDEN_AGENT_MODEL_MODELS. It must be one of the allowed models.
    agent_model_default: str = ""
    # Where the garden CLI and dsh files come from: the built zipapp, the dsh bundles, the dsh
    # config and the install scripts. The portal serves only the names it offers, from this folder.
    agent_downloads_dir: Path = Path("var/agent-downloads")

    @property
    def allowed_model_names(self) -> list[str]:
        """The allowed models in the order the setting lists them, without repeats."""
        names: list[str] = []
        for part in self.agent_model_models.split(","):
            name = part.strip()
            if name and name not in names:
                names.append(name)
        return names

    @property
    def allowed_models(self) -> frozenset[str]:
        return frozenset(self.allowed_model_names)

    @property
    def agent_mcp_origins(self) -> frozenset[str]:
        return frozenset(
            origin.strip() for origin in self.agent_mcp_allowed_origins.split(",") if origin.strip()
        )

    @property
    def default_model(self) -> str | None:
        """The model the gateway names as `default`: the configured one, else the first allowed."""
        if self.agent_model_default:
            return self.agent_model_default
        return self.allowed_model_names[0] if self.allowed_model_names else None

    @model_validator(mode="after")
    def refuse_fake_oidc_outside_dev(self) -> Self:
        if self.fake_oidc and self.environment != "dev":
            raise ValueError("GARDEN_FAKE_OIDC is only allowed when GARDEN_ENVIRONMENT=dev")
        return self

    @model_validator(mode="after")
    def model_default_is_allowed(self) -> Self:
        if self.agent_model_default and self.agent_model_default not in self.allowed_models:
            raise ValueError("GARDEN_AGENT_MODEL_DEFAULT must be one of GARDEN_AGENT_MODEL_MODELS")
        return self

    @model_validator(mode="after")
    def mcp_wait_limits_fit_the_worker_pool(self) -> Self:
        if self.agent_mcp_pending_per_login < 1 or self.agent_mcp_pending_total < 1:
            raise ValueError("GARDEN_AGENT_MCP_PENDING limits must be positive")
        if self.agent_mcp_pending_per_login > self.agent_mcp_pending_total:
            raise ValueError("GARDEN_AGENT_MCP_PENDING_PER_LOGIN cannot exceed PENDING_TOTAL")
        if self.agent_mcp_pending_total >= 40:
            raise ValueError("GARDEN_AGENT_MCP_PENDING_TOTAL must be below the 40-thread MCP pool")
        return self

    @model_validator(mode="after")
    def require_https_outside_dev(self) -> Self:
        if self.environment != "dev" and not self.public_url.startswith("https://"):
            raise ValueError("GARDEN_PUBLIC_URL must use https outside dev")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # database_url comes from the environment

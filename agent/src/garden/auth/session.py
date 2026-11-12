"""Session and credential management for the Garden CLI."""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from garden.auth.browser import open_link
from garden.auth.listener import DEFAULT_TIMEOUT_SECONDS, LoopbackLoginServer
from garden.auth.secrets import SecretStore, default_store
from garden.shared import (
    GardenError,
    NotSignedIn,
    Settings,
    as_object,
    as_text,
    load_settings,
)


@dataclass(frozen=True)
class Login:
    token: str
    portal_url: str = ""
    created_at: str | None = None
    device: str = ""

    def to_json(self) -> str:
        body: dict[str, Any] = {
            "token": self.token,
            "device": self.device,
        }
        if self.created_at is not None:
            body["created_at"] = self.created_at
        return json.dumps(body)

    @classmethod
    def from_json(cls, raw: str | dict[str, Any], *, portal_url: str = "") -> Login:
        data = as_object(json.loads(raw)) if isinstance(raw, str) else as_object(raw)
        return cls(
            token=str(data["token"]),
            portal_url=portal_url,
            created_at=data.get("created_at"),
            device=str(data.get("device", "")),
        )


def device_name() -> str:
    try:
        return socket.gethostname() or "garden-cli"
    except OSError:
        return "garden-cli"


def load_session(
    settings: Settings | None = None,
    *,
    store: SecretStore | None = None,
) -> Login | None:
    active_settings = settings or load_settings()
    active_store = store or default_store(active_settings.home)
    raw = active_store.get(active_settings.portal_url)
    if not raw:
        return None

    try:
        return Login.from_json(raw, portal_url=active_settings.portal_url)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def require_session(
    settings: Settings | None = None,
    *,
    store: SecretStore | None = None,
) -> Login:
    session = load_session(settings, store=store)
    if session is None:
        raise NotSignedIn()
    return session


def store_token(
    token: str,
    *,
    portal_url: str | None = None,
    device: str = "test-laptop",
    created_at: str | None = None,
    settings: Settings | None = None,
    store: SecretStore | None = None,
) -> Login:
    resolved_portal = portal_url or (settings or load_settings()).portal_url
    active_store = store or default_store(settings.home if settings else None)
    record = Login(
        token=token,
        portal_url=resolved_portal,
        created_at=created_at,
        device=device,
    )
    active_store.set(resolved_portal, record.to_json())
    return record


def _default_exchange_code(
    path: str, payload: dict[str, object], portal_url: str
) -> Mapping[str, Any]:
    url = f"{portal_url.rstrip('/')}{path}"
    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            resp_bytes: bytes = resp.read()
            resp_text = resp_bytes.decode("utf-8")
            parsed: object = json.loads(resp_text)
            return as_object(parsed)
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException):
            detail = ""
        raise GardenError(
            title="Code exchange failed",
            detail=detail,
            status=error.code,
            fix="Run garden login again.",
        ) from error
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        reason = getattr(error, "reason", None) or str(error) or type(error).__name__ or "timed out"
        raise GardenError(
            title="Could not connect to portal",
            detail=str(reason),
            fix="Check your network connection and portal address.",
        ) from error


def login(
    *,
    settings: Settings | None = None,
    store: SecretStore | None = None,
    portal_url: str | None = None,
    device: str | None = None,
    open_browser: bool = True,
    on_authorize_url: Callable[[str], None] | None = None,
    exchange_code: Callable[[str, dict[str, object]], Mapping[str, Any]] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Login:
    resolved_portal = portal_url or (settings or load_settings()).portal_url
    resolved_device = device or device_name()

    with LoopbackLoginServer(
        portal_url=resolved_portal,
        device_name=resolved_device,
        timeout_seconds=timeout_seconds,
    ) as server:
        if on_authorize_url is not None:
            on_authorize_url(server.authorize_url)
        else:
            print(f"Approve this login for {resolved_device} in your browser:")
            print(f"  {server.authorize_url}", flush=True)
        if open_browser and not open_link(server.authorize_url):
            print(
                "No browser could be opened here. "
                "Open the link above yourself to approve the login.",
                flush=True,
            )
        result = server.wait_for_callback()

    payload: dict[str, object] = {
        "code": result.code,
        "code_verifier": result.verifier,
        "redirect_uri": result.redirect_uri,
        "nonce": result.nonce,
        "device": resolved_device,
    }

    def default_exchange(ep: str, b: dict[str, object]) -> Mapping[str, Any]:
        return _default_exchange_code(ep, b, resolved_portal)

    exchange_fn = exchange_code or default_exchange
    result_data = exchange_fn("/api/agent/cli/approvals", payload)
    token = as_text(result_data.get("token") or result_data.get("access_token"))
    if not token:
        raise GardenError(title="The portal sent no login", fix="Run garden login again.")

    created_at_val = result_data.get("created_at")
    created_at = str(created_at_val) if created_at_val is not None else None

    return store_token(
        token,
        portal_url=resolved_portal,
        created_at=created_at,
        device=resolved_device,
        settings=settings,
        store=store,
    )


def logout(
    *,
    settings: Settings | None = None,
    store: SecretStore | None = None,
    revoke_remote: Callable[[str], None] | None = None,
    force: bool = False,
) -> tuple[Login | None, str | None]:
    active_settings = settings or load_settings()
    active_store = store or default_store(active_settings.home)
    current = load_session(active_settings, store=active_store)
    warning: str | None = None
    if current is not None and revoke_remote is not None:
        try:
            revoke_remote(current.token)
        except GardenError as error:
            warning = error.title
        except (OSError, http.client.HTTPException):
            warning = "The portal cannot be reached"
    if warning is None or force:
        active_store.delete(active_settings.portal_url)
    return (current, warning)

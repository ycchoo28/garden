"""Helpers shared by the backend tests: sign-in as a seeded persona, CLI sign-in and bearer headers.

The fixtures live in backend/conftest.py. These are plain functions, so tests import them by name.
"""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient

from app.core.users.identity import IdentityClaims
from tests.fakes import GOOD_CODE, FakeIdentity


async def sign_in(client: AsyncClient, identity: FakeIdentity, subject: str) -> None:
    """Run the browser sign-in as the given persona. The session cookie stays on the client."""
    identity.claims = IdentityClaims(subject=subject, email=subject, name=subject)
    started = await client.get("/auth/login", params={"return_to": "/"}, follow_redirects=False)
    assert started.status_code == 302
    state_value = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    finished = await client.get(
        "/auth/callback",
        params={"code": GOOD_CODE, "state": state_value},
        follow_redirects=False,
    )
    assert finished.status_code == 302, finished.text


async def cli_sign_in(
    client: AsyncClient, identity: FakeIdentity, subject: str, device: str = "test-laptop"
) -> str:
    """Run the CLI sign-in as the given persona, with PKCE, and return the bearer token it gets."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    redirect_uri = "http://127.0.0.1:53111/callback"
    nonce = secrets.token_urlsafe(16)
    identity.claims = IdentityClaims(subject=subject, email=subject, name=subject)
    started = await client.get(
        "/api/agent/cli/authorize",
        params={
            "redirect_uri": redirect_uri,
            "state": secrets.token_urlsafe(16),
            "nonce": nonce,
            "code_challenge": challenge,
        },
        follow_redirects=False,
    )
    assert started.status_code == 302, started.text
    finished = await client.post(
        "/api/agent/cli/approvals",
        json={
            "code": GOOD_CODE,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "device": device,
        },
    )
    assert finished.status_code == 201, finished.text
    return str(finished.json()["token"])


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}

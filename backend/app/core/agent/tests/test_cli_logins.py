"""CLI logins: the PKCE sign-in, what a CLI login may do, and revoking one from the browser."""

import base64
import hashlib
import secrets

import pytest
from httpx import AsyncClient

from tests.fakes import FakeIdentity
from tests.support import bearer, cli_sign_in, sign_in

MARCUS = "marcus.lee@fernhill.example"
PRIYA = "priya.nair@fernhill.example"
LOOPBACK = "http://127.0.0.1:53111/callback"


@pytest.mark.asyncio
async def test_authorize_sends_the_browser_to_the_identity_provider_with_the_cli_challenge(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(b"verifier").digest()).rstrip(b"=")
    started = await client.get(
        "/api/agent/cli/authorize",
        params={
            "redirect_uri": LOOPBACK,
            "state": secrets.token_urlsafe(16),
            "nonce": secrets.token_urlsafe(16),
            "code_challenge": challenge.decode(),
        },
        follow_redirects=False,
    )
    assert started.status_code == 302, started.text
    assert started.headers["location"].startswith("https://idp.example.test/authorize")
    assert identity.last_challenge == challenge.decode()


@pytest.mark.asyncio
async def test_authorize_refuses_a_callback_that_is_not_on_this_machine(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/api/agent/cli/authorize",
        params={
            "redirect_uri": "https://attacker.example/callback",
            "state": secrets.token_urlsafe(16),
            "nonce": secrets.token_urlsafe(16),
            "code_challenge": secrets.token_urlsafe(32),
        },
        follow_redirects=False,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cli_login_acts_as_the_person_without_person_only_permissions(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    token = await cli_sign_in(client, identity, MARCUS)
    me = await client.get("/api/users/me", headers=bearer(token))
    assert me.status_code == 200, me.text
    permissions = me.json()["permissions"]
    assert "files.upload" in permissions
    assert "agent.logins.view" in permissions
    assert "agent.logins.revoke" not in permissions
    assert "agent.confirmations.decide" not in permissions


@pytest.mark.asyncio
async def test_person_revokes_a_cli_login_in_the_browser_and_its_token_stops(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    token = await cli_sign_in(client, identity, MARCUS, device="work-laptop")
    await sign_in(client, identity, MARCUS)
    listed = await client.get("/api/agent/my-logins")
    assert listed.status_code == 200, listed.text
    [login] = listed.json()
    assert login["device"] == "work-laptop"
    assert "token" not in login

    revoked = await client.post(f"/api/agent/my-logins/{login['id']}/sign-out")
    assert revoked.status_code == 204, revoked.text
    after = await client.get("/api/users/me", headers=bearer(token))
    assert after.status_code == 401


@pytest.mark.asyncio
async def test_an_agent_cannot_revoke_its_own_login(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    token = await cli_sign_in(client, identity, MARCUS)
    listed = await client.get("/api/agent/my-logins", headers=bearer(token))
    assert listed.status_code == 200, listed.text
    [login] = listed.json()
    refused = await client.post(
        f"/api/agent/my-logins/{login['id']}/sign-out", headers=bearer(token)
    )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_a_person_cannot_revoke_someone_elses_login(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    token = await cli_sign_in(client, identity, MARCUS)
    listed = await client.get("/api/agent/my-logins", headers=bearer(token))
    [login] = listed.json()
    await sign_in(client, identity, PRIYA)
    refused = await client.post(f"/api/agent/my-logins/{login['id']}/sign-out")
    assert refused.status_code == 404


@pytest.mark.asyncio
async def test_a_cli_login_ends_its_own_token_and_a_browser_call_is_refused(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    token = await cli_sign_in(client, identity, MARCUS, device="work-laptop")
    await sign_in(client, identity, MARCUS)
    refused = await client.delete("/api/agent/cli/token")
    assert refused.status_code == 403, refused.text

    signed_out = await client.delete("/api/agent/cli/token", headers=bearer(token))
    assert signed_out.status_code == 204, signed_out.text
    after = await client.get("/api/users/me", headers=bearer(token))
    assert after.status_code == 401
    still_browsing = await client.get("/api/users/me")
    assert still_browsing.status_code == 200, still_browsing.text

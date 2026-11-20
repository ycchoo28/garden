"""OIDC discovery retries transient startup delays and returns a problem when unavailable."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, ReadTimeout, Request, Response

from app.core.users.oidc import OidcIdentityProvider
from app.shared.errors import install_problem_handlers


def _provider(http: AsyncClient) -> OidcIdentityProvider:
    return OidcIdentityProvider(
        issuer="http://oidc.test",
        client_id="garden-demo",
        client_secret="test-secret",
        http=http,
    )


@pytest.mark.asyncio
async def test_discovery_retries_after_a_temporary_read_timeout() -> None:
    requests = 0

    def respond(request: Request) -> Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            raise ReadTimeout("fake OIDC is starting", request=request)
        return Response(200, json={"authorization_endpoint": "http://oidc.test/authorize"})

    async with AsyncClient(transport=MockTransport(respond)) as http:
        url = await _provider(http).authorization_url(
            redirect_uri="http://127.0.0.1:53111/callback",
            state="state",
            nonce="nonce",
            code_challenge="challenge",
        )

    assert requests == 2
    assert url.startswith("http://oidc.test/authorize?")


@pytest.mark.asyncio
async def test_discovery_timeout_returns_a_retryable_problem_response() -> None:
    requests = 0

    def timeout(request: Request) -> Response:
        nonlocal requests
        requests += 1
        raise ReadTimeout("fake OIDC is still unavailable", request=request)

    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/authorize")
    async def authorize() -> dict[str, str]:
        return {
            "url": await _provider(upstream).authorization_url(
                redirect_uri="http://127.0.0.1:53111/callback",
                state="state",
                nonce="nonce",
                code_challenge="challenge",
            )
        }

    async with (
        AsyncClient(transport=MockTransport(timeout)) as upstream,
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        response = await client.get("/authorize")

    assert requests == 3
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["retry-after"] == "2"
    assert response.json()["detail"] == (
        "The sign-in provider is not responding. Try again in a moment."
    )

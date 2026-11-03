"""The OIDC adapter for the identity port: authorization code flow with PKCE and checked ID tokens.

It works with any standard OIDC provider, and with the fake one in dev/compose.yaml.
"""

from collections.abc import Sequence
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Depends

from app.config import Settings, get_settings
from app.core.users.identity import IdentityClaims, IdentityProvider, IdentityRejected, TokenSet

# Asymmetric algorithms only. A token signed with a shared secret is never accepted.
SIGNING_ALGORITHMS: Sequence[str] = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
)


class OidcIdentityProvider:
    def __init__(
        self, *, issuer: str, client_id: str, client_secret: str, http: httpx.AsyncClient
    ) -> None:
        # A token's iss must match the issuer exactly, trailing slash included.
        self._issuer = issuer
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http

    async def _discovery(self) -> dict[str, Any]:
        base = self._issuer.rstrip("/")
        response = await self._http.get(f"{base}/.well-known/openid-configuration")
        _ = response.raise_for_status()
        discovery: dict[str, Any] = response.json()
        return discovery

    async def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str
    ) -> str:
        endpoint = (await self._discovery())["authorization_endpoint"]
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "scope": "openid profile email",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}?{query}"

    async def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        endpoint = (await self._discovery())["token_endpoint"]
        # The client credentials go in the form body (client_secret_post), not in a Basic header.
        response = await self._http.post(
            endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if 400 <= response.status_code < 500:
            raise IdentityRejected("The provider refused the code.")
        _ = response.raise_for_status()
        body: dict[str, Any] = response.json()
        if "id_token" not in body:
            raise IdentityRejected("The provider did not return an ID token.")
        return TokenSet(id_token=str(body["id_token"]))

    async def verify_id_token(self, id_token: str, *, nonce: str) -> IdentityClaims:
        jwks_uri = (await self._discovery())["jwks_uri"]
        jwks: dict[str, Any] = (await self._http.get(jwks_uri)).raise_for_status().json()
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
            keys = [key for key in jwt.PyJWKSet.from_dict(jwks).keys if key.key_id == kid]
            if not keys:
                raise IdentityRejected("The ID token is signed with an unknown key.")
            claims: dict[str, Any] = jwt.decode(
                id_token,
                keys[0].key,
                algorithms=list(SIGNING_ALGORITHMS),
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as error:
            raise IdentityRejected(str(error)) from error
        if claims.get("nonce") != nonce:
            raise IdentityRejected("The ID token nonce does not match this sign-in.")
        subject = str(claims["sub"])
        email = str(claims.get("email", ""))
        return IdentityClaims(
            subject=subject,
            email=email,
            name=str(claims.get("name") or email or subject),
        )


@lru_cache
def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)


async def get_identity_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> IdentityProvider:
    return OidcIdentityProvider(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        http=_http_client(),
    )

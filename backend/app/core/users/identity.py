"""The identity port: what the users module needs from the identity provider.

The sign-in service depends on this port only. The OIDC adapter and the test fake implement it.
"""

from dataclasses import dataclass
from typing import Protocol


class IdentityRejected(Exception):
    """The identity provider refused the code, or the ID token did not check out."""


@dataclass(frozen=True)
class TokenSet:
    id_token: str


@dataclass(frozen=True)
class IdentityClaims:
    subject: str
    email: str
    name: str


class IdentityProvider(Protocol):
    async def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str
    ) -> str: ...

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet: ...

    async def verify_id_token(self, id_token: str, *, nonce: str) -> IdentityClaims: ...

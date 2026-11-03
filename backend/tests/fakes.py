"""Test doubles: a fake identity provider that checks PKCE and nonce, and a movable clock."""

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.users.identity import IdentityClaims, IdentityRejected, TokenSet

GOOD_CODE = "good-code"


@dataclass
class FakeIdentity:
    """Signs people in without a network. The test sets who the next sign-in is."""

    claims: IdentityClaims | None = None
    last_challenge: str = ""
    nonces: set[str] = field(default_factory=set)

    async def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str
    ) -> str:
        self.last_challenge = code_challenge
        self.nonces.add(nonce)
        return f"https://idp.example.test/authorize?state={state}&nonce={nonce}"

    async def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        if code != GOOD_CODE:
            raise IdentityRejected("refused")
        digest = hashlib.sha256(code_verifier.encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != self.last_challenge:
            raise IdentityRejected("PKCE verifier does not match the challenge")
        return TokenSet(id_token=f"id-token-{len(self.nonces)}")

    async def verify_id_token(self, id_token: str, *, nonce: str) -> IdentityClaims:
        if nonce not in self.nonces or self.claims is None:
            raise IdentityRejected("nonce mismatch")
        return self.claims


@dataclass
class MovableClock:
    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current = self.current + delta

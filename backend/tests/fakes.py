"""Test doubles: a fake identity provider that checks PKCE and nonce, and a movable clock."""

import base64
import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.users.identity import IdentityClaims, IdentityRejected, TokenSet
from app.modules.knowledge.embedding import EMBEDDING_DIMENSIONS

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


@dataclass
class FakeEmbedder:
    """A deterministic stand-in for bge-m3.

    Texts that share words get similar vectors, so ranking is testable.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_hashed_vector(text) for text in texts]


def _hashed_vector(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        vector[0] = 1.0
        return vector
    return [value / length for value in vector]

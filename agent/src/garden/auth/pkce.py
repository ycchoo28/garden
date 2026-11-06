"""PKCE values for the browser login (RFC 7636, S256 only)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Challenge:
    verifier: str
    challenge: str
    method: str = "S256"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_verifier() -> str:
    return _b64url(secrets.token_bytes(48))


def challenge_for(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def generate_challenge() -> Challenge:
    verifier = new_verifier()
    return Challenge(verifier=verifier, challenge=challenge_for(verifier))

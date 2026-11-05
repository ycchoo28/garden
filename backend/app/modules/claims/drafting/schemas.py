"""Request shapes for drafting a claim: create it, add a line, void a line."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from app.modules.claims.schemas import VersionIn

Money = Annotated[Decimal, Field(gt=Decimal("0"), max_digits=14, decimal_places=2)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CreateClaimIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class AddLineIn(VersionIn):
    category: str = Field(min_length=1, max_length=40)
    spent_on: date
    amount: Money
    description: str = Field(min_length=1, max_length=300)
    receipt_sha256: Sha256


class VoidLineIn(VersionIn):
    reason: str | None = Field(default=None, max_length=300)

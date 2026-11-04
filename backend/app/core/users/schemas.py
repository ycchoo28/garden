"""Request and response bodies of the users routes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.users.models import CliLogin
from app.core.users.service import CurrentPerson, PersonView


class DepartmentOut(BaseModel):
    code: str
    name: str


class MeOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    department: DepartmentOut
    roles: list[str]
    permissions: list[str]

    @classmethod
    def of(cls, person: CurrentPerson) -> "MeOut":
        return cls(
            id=person.id,
            name=person.name,
            email=person.email,
            department=DepartmentOut(code=person.department_code, name=person.department_name),
            roles=sorted(person.roles),
            permissions=sorted(person.permissions),
        )


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    department: DepartmentOut

    @classmethod
    def of(cls, view: PersonView) -> "PersonOut":
        return cls(
            id=view.id,
            name=view.name,
            email=view.email,
            department=DepartmentOut(code=view.department_code, name=view.department_name),
        )


class CliLoginIn(BaseModel):
    """What the CLI sends after its browser returned the code: the PKCE verifier and the nonce."""

    code: str = Field(min_length=1, max_length=2048)
    code_verifier: str = Field(min_length=43, max_length=128)
    redirect_uri: str = Field(max_length=300)
    nonce: str = Field(min_length=16, max_length=128)
    device: str = Field(min_length=1, max_length=120)


class CliLoginCreated(BaseModel):
    """The new CLI login. `token` is shown once, and only its hash is kept."""

    id: uuid.UUID
    device: str
    created_at: datetime
    token: str


class CliLoginOut(BaseModel):
    id: uuid.UUID
    device: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def of(cls, row: CliLogin) -> "CliLoginOut":
        return cls(
            id=row.id,
            device=row.device,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            revoked_at=row.revoked_at,
        )

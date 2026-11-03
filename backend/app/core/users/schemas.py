"""Request and response bodies of the users routes."""

import uuid

from pydantic import BaseModel

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

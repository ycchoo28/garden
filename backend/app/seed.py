"""Idempotent development data: Fernhill Studio departments and personas. Run `just seed`."""

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.core.approvals.models import Flow, FlowStep
from app.core.users.models import Department, Person, RoleGrant
from app.modules.claims import CLAIM_FLOW_CODE, CLAIM_FLOW_NAME, CLAIM_FLOW_STEPS
from app.shared.db import session_factory
from app.shared.ids import new_id

DEPARTMENTS: tuple[tuple[str, str], ...] = (
    ("design", "Design"),
    ("engineering", "Engineering"),
    ("marketing", "Marketing"),
    ("operations", "Operations"),
)

# The subject is the email, which is also the `sub` that the fake sign-in provider issues.
PERSONAS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("dev.admin@fernhill.example", "Dev Admin", "operations", ("admin",)),
    ("priya.nair@fernhill.example", "Priya Nair", "design", ("manager",)),
    ("marcus.lee@fernhill.example", "Marcus Lee", "design", ()),
    ("ana.costa@fernhill.example", "Ana Costa", "engineering", ("manager",)),
    ("tomas.berg@fernhill.example", "Tomas Berg", "engineering", ()),
    ("mei.tan@fernhill.example", "Mei Tan", "marketing", ("manager",)),
    ("sam.okafor@fernhill.example", "Sam Okafor", "marketing", ()),
    ("lena.fischer@fernhill.example", "Lena Fischer", "operations", ("finance",)),
    ("noor.haddad@fernhill.example", "Noor Haddad", "operations", ("knowledge_admin",)),
)


async def seed() -> None:
    async with session_factory(get_settings().database_url)() as session:
        departments: dict[str, Department] = {}
        for code, name in DEPARTMENTS:
            row = await session.scalar(select(Department).where(Department.code == code))
            if row is None:
                row = Department(id=new_id(), code=code, name=name)
                session.add(row)
            departments[code] = row
        await session.flush()

        for subject, name, department_code, roles in PERSONAS:
            person = await session.scalar(select(Person).where(Person.subject == subject))
            if person is None:
                person = Person(
                    id=new_id(),
                    subject=subject,
                    email=subject,
                    name=name,
                    department_id=departments[department_code].id,
                    is_active=True,
                )
                session.add(person)
                await session.flush()
            for role in roles:
                granted = await session.scalar(
                    select(RoleGrant).where(
                        RoleGrant.person_id == person.id, RoleGrant.role == role
                    )
                )
                if granted is None:
                    session.add(RoleGrant(id=new_id(), person_id=person.id, role=role))

        # Approval flows are data, so the seed loads each one that is missing.
        if await session.get(Flow, CLAIM_FLOW_CODE) is None:
            session.add(Flow(code=CLAIM_FLOW_CODE, name=CLAIM_FLOW_NAME))
            await session.flush()
        for step in CLAIM_FLOW_STEPS:
            existing = await session.scalar(
                select(FlowStep).where(
                    FlowStep.flow_code == CLAIM_FLOW_CODE, FlowStep.position == step.position
                )
            )
            if existing is None:
                session.add(
                    FlowStep(
                        id=new_id(),
                        flow_code=CLAIM_FLOW_CODE,
                        position=step.position,
                        role=step.role,
                        department_scoped=step.department_scoped,
                        above=step.above,
                    )
                )
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())

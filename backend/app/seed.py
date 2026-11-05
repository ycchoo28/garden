"""Idempotent development data: Fernhill Studio departments and personas. Run `just seed`."""

import asyncio
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.approvals.models import Flow, FlowStep
from app.core.users.models import Department, Person, RoleGrant
from app.modules.claims import CLAIM_FLOW_CODE, CLAIM_FLOW_NAME, CLAIM_FLOW_STEPS, FlowStepSeed
from app.modules.knowledge import (
    KNOWLEDGE_FLOW_CODE,
    KNOWLEDGE_FLOW_NAME,
    KNOWLEDGE_FLOW_STEPS,
    KnowledgeFlowStep,
)
from app.shared.db import session_factory
from app.shared.ids import new_id

DEPARTMENTS: tuple[tuple[str, str], ...] = (
    ("design", "Design"),
    ("operations", "Operations"),
)

# The subject is the email, which is also the `sub` that the fake sign-in provider issues.
PERSONAS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("marcus.lee@fernhill.example", "Marcus Lee", "design", ()),
    ("priya.nair@fernhill.example", "Priya Nair", "design", ("manager",)),
    ("lena.fischer@fernhill.example", "Lena Fischer", "operations", ("finance",)),
    # The admin is also the Operations manager, and approves the knowledge batches Lena starts.
    ("dev.admin@fernhill.example", "Dev Admin", "operations", ("admin", "manager")),
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
        await _load_flow(session, CLAIM_FLOW_CODE, CLAIM_FLOW_NAME, CLAIM_FLOW_STEPS)
        await _load_flow(session, KNOWLEDGE_FLOW_CODE, KNOWLEDGE_FLOW_NAME, KNOWLEDGE_FLOW_STEPS)
        await session.commit()


async def _load_flow(
    session: AsyncSession,
    code: str,
    name: str,
    steps: Sequence[FlowStepSeed | KnowledgeFlowStep],
) -> None:
    if await session.get(Flow, code) is None:
        session.add(Flow(code=code, name=name))
        await session.flush()
    for step in steps:
        existing = await session.scalar(
            select(FlowStep).where(FlowStep.flow_code == code, FlowStep.position == step.position)
        )
        if existing is None:
            session.add(
                FlowStep(
                    id=new_id(),
                    flow_code=code,
                    position=step.position,
                    role=step.role,
                    department_scoped=step.department_scoped,
                    above=step.above,
                )
            )


if __name__ == "__main__":
    asyncio.run(seed())

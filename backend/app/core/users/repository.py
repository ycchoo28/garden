"""Users database access. Only this module and models.py touch the database."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.users.models import Department, Person, RoleGrant, SignInAttempt, WebSession
from app.shared.db import get_session


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def person_by_subject(self, subject: str) -> Person | None:
        return await self._session.scalar(select(Person).where(Person.subject == subject))

    async def department_by_id(self, department_id: uuid.UUID) -> Department | None:
        return await self._session.get(Department, department_id)

    async def departments(self) -> Sequence[Department]:
        return (await self._session.scalars(select(Department).order_by(Department.code))).all()

    async def role_names(self, person_id: uuid.UUID) -> frozenset[str]:
        names = await self._session.scalars(
            select(RoleGrant.role).where(RoleGrant.person_id == person_id)
        )
        return frozenset(names)

    async def people_in(
        self, departments: frozenset[uuid.UUID] | None, include: uuid.UUID
    ) -> Sequence[tuple[Person, Department]]:
        """Active people in the given departments (all when None), plus the caller themselves."""
        query = (
            select(Person, Department)
            .join(Department, Department.id == Person.department_id)
            .where(Person.is_active)
            .order_by(Department.code, Person.name)
        )
        if departments is not None:
            query = query.where(or_(Person.department_id.in_(departments), Person.id == include))
        return (await self._session.execute(query)).all()

    async def add_sign_in_attempt(self, attempt: SignInAttempt) -> None:
        self._session.add(attempt)

    async def take_sign_in_attempt(self, state: str) -> SignInAttempt | None:
        attempt = await self._session.get(SignInAttempt, state)
        if attempt is not None:
            await self._session.delete(attempt)
        return attempt

    async def purge_sign_in_attempts(self, created_before: datetime) -> None:
        _ = await self._session.execute(
            delete(SignInAttempt).where(SignInAttempt.created_at < created_before)
        )

    def add_web_session(self, row: WebSession) -> None:
        self._session.add(row)

    async def web_session_with_person(self, token_hash: str) -> tuple[WebSession, Person] | None:
        row = (
            await self._session.execute(
                select(WebSession, Person)
                .join(Person, Person.id == WebSession.person_id)
                .where(WebSession.token_hash == token_hash)
            )
        ).first()
        return None if row is None else (row[0], row[1])

    async def touch_web_session(self, token_hash: str, seen_at: datetime) -> None:
        _ = await self._session.execute(
            update(WebSession)
            .where(WebSession.token_hash == token_hash)
            .values(last_seen_at=seen_at)
        )

    async def delete_web_session(self, token_hash: str) -> None:
        _ = await self._session.execute(
            delete(WebSession).where(WebSession.token_hash == token_hash)
        )


def get_user_repository(session: Annotated[AsyncSession, Depends(get_session)]) -> UserRepository:
    return UserRepository(session)

"""Budget-owned reporting query for current budgets and approved claim spend."""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.budgets.variance.views import VarianceRow
from app.shared.db import get_session


class VarianceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rows(
        self,
        *,
        first_month: str,
        last_month: str,
        department_ids: frozenset[uuid.UUID] | None,
    ) -> Sequence[VarianceRow]:
        scope_clause = "" if department_ids is None else "and department_id in :department_ids"
        actual_scope_clause = (
            "" if department_ids is None else "and c.department_id in :department_ids"
        )
        query = text(
            f"""
            with latest_runs as (
                select subject_id, id, started_at, cancelled_at,
                       row_number() over (
                           partition by subject_id order by started_at desc, id desc
                       ) as run_order
                from approvals.run
                where subject_type = 'claim'
            ), run_states as (
                select latest.subject_id,
                       case
                           when latest.cancelled_at is not null then 'cancelled'
                           when exists (
                               select 1 from approvals.decision d
                               where d.run_id = latest.id and d.outcome = 'rejected'
                           ) then 'rejected'
                           when exists (
                               select 1 from approvals.run_step s where s.run_id = latest.id
                           ) and not exists (
                               select 1 from approvals.run_step s
                               where s.run_id = latest.id and not exists (
                                   select 1 from approvals.decision d
                                   where d.run_id = s.run_id and d.position = s.position
                               )
                           ) then 'approved'
                           else 'pending'
                       end as state
                from latest_runs latest
                where latest.run_order = 1
            ), spend as (
                select c.department_id,
                       to_char(l.spent_on, 'YYYY-MM') as year_month,
                       l.category,
                       sum(l.amount) as actual
                from claims.claim c
                join run_states r on r.subject_id = c.id and r.state = 'approved'
                join claims.line l on l.claim_id = c.id and l.voided_at is null
                where to_char(l.spent_on, 'YYYY-MM') >= :first_month
                  and to_char(l.spent_on, 'YYYY-MM') <= :last_month
                  {actual_scope_clause}
                group by c.department_id, to_char(l.spent_on, 'YYYY-MM'), l.category
            ), current_budgets as (
                select department_id, year_month, category, amount as budget
                from budgets.line
                where superseded_at is null
                  and year_month >= :first_month and year_month <= :last_month
                  {scope_clause}
            )
            select coalesce(b.department_id, s.department_id) as department_id,
                   coalesce(d.code, 'unknown') as department_code,
                   coalesce(d.name, 'Unknown') as department_name,
                   coalesce(b.year_month, s.year_month) as year_month,
                   coalesce(b.category, s.category) as category,
                   b.budget,
                   coalesce(s.actual, 0::numeric) as actual
            from current_budgets b
            full outer join spend s using (department_id, year_month, category)
            left join users.department d on d.id = coalesce(b.department_id, s.department_id)
            order by coalesce(d.code, 'unknown'), coalesce(b.year_month, s.year_month),
                     coalesce(b.category, s.category)
            """
        )
        if department_ids is not None:
            query = query.bindparams(bindparam("department_ids", expanding=True))
        params: dict[str, object] = {"first_month": first_month, "last_month": last_month}
        if department_ids is not None:
            params["department_ids"] = list(department_ids)
        result = await self._session.execute(query, params)
        return [VarianceRow(**row) for row in result.mappings().all()]


def get_variance_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VarianceRepository:
    return VarianceRepository(session)

"""Request and response bodies of the audit routes."""

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: str
    occurred_at: str
    actor_id: str
    acting_as_agent: bool
    operation: str
    target: str
    summary: str

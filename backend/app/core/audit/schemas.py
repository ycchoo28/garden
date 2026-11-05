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
    # The approval that let the change run, when the person approved it in the browser.
    confirmation_id: str | None
    confirmed_at: str | None

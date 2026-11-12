"""How the current request is signed in, so the audit trail can say whether an agent made a save.

The users module sets this when it resolves the person behind a request, and the audit module reads
it when it records a save. Modules change nothing: every save made through a CLI login is marked
the same way, with the login's device and creation time.
"""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

# "web" for the browser's session cookie, "agent" for a CLI login's bearer token.
VIA_WEB = "web"
VIA_AGENT = "agent"


@dataclass(frozen=True)
class RequestLogin:
    via: str
    source: str | None = None
    device: str | None = None
    created_at: datetime | None = None
    # The CLI login's id, so the model gateway can count one login's requests on their own.
    cli_login_id: uuid.UUID | None = None
    # Set when a CLI login's critical action ran under a confirmation the person approved in the
    # browser: the confirmation's id and when they approved it.
    confirmation_id: str | None = None
    confirmed_at: datetime | None = None


# None outside a request, such as in a seed script or a background job. Those saves are not marked.
current_request_login: ContextVar[RequestLogin | None] = ContextVar(
    "current_request_login", default=None
)

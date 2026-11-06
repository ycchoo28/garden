"""Demo data for Fernhill Studio: budgets, closed months, claims and the policy library.

Run `just demo`, or `uv run python -m app.demo_seed` on a migrated database.
The seed drives the same HTTP routes the browser uses, in process, signing in as each persona.
So every change gets its audit row, approval run and validity span, as a person would make it.
Each step looks for what is already saved before it writes, so a second run changes nothing.
The minimal persona seed in `app.seed` stays as it is, because the browser tests depend on it
and not on this module.
"""

import asyncio
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select

from app.composition import MODULES
from app.config import get_settings
from app.core.users.identity import IdentityClaims, IdentityRejected, TokenSet
from app.core.users.oidc import get_identity_provider
from app.main import create_app
from app.modules.budgets import CATEGORIES
from app.modules.knowledge.batches.jobs import get_batch_queue, run_prepare_job
from app.modules.knowledge.batches.lifecycle import OPEN_STATES, BatchState
from app.modules.knowledge.reading.embedding import Embedder, get_embedder
from app.seed import seed
from app.shared.db import session_factory

logger = logging.getLogger(__name__)

POLICY_DIR = Path(__file__).resolve().parent / "demo_data" / "policies"
GOOD_CODE = "demo-code"
# Policy files whose number is at most this go to the expense collection, and the rest to finance.
EXPENSE_FILE_LIMIT = 6

EXPENSE_COLLECTION = "Expense policy"
FINANCE_COLLECTION = "Finance procedures"
EXPENSE_RULES: tuple[dict[str, object], ...] = ({"role": "employee", "all_departments": True},)
FINANCE_RULES: tuple[dict[str, object], ...] = ({"role": "finance", "all_departments": True},)

LENA = "lena.fischer@fernhill.example"
DEV_ADMIN = "dev.admin@fernhill.example"
MARCUS = "marcus.lee@fernhill.example"
PRIYA = "priya.nair@fernhill.example"

DEPARTMENT_CODES: tuple[str, ...] = ("design", "operations")

# Monthly budget in US dollars per department and spend category.
MONTHLY_BASE: Mapping[str, Mapping[str, int]] = {
    "design": {
        "equipment": 2400, "marketing": 3500, "meals": 700, "office": 900,
        "other": 400, "services": 4200, "software": 1800, "travel": 2200,
    },
    "operations": {
        "equipment": 1200, "marketing": 400, "meals": 500, "office": 2600,
        "other": 800, "services": 2300, "software": 1400, "travel": 900,
    },
}  # fmt: skip

# One budget line is replaced once, so its history has a reason. Design software grew.
REPLACED_DEPARTMENT = "design"
REPLACED_CATEGORY = "software"
REPLACED_AMOUNT = Decimal("2400.00")
REPLACED_REASON = "A licence seat was added for the design team, so the software budget went up."


class DemoSeedError(RuntimeError):
    """The seed could not reach the state it needs. The message names the step that failed."""


@dataclass(frozen=True)
class ClaimLine:
    category: str
    day: int
    amount: str
    description: str


@dataclass(frozen=True)
class ClaimStep:
    actor: str
    action: str  # submit, withdraw, approve or reject
    reason: str | None = None


@dataclass(frozen=True)
class ClaimSpec:
    title: str
    claimant: str
    lines: tuple[ClaimLine, ...]
    steps: tuple[ClaimStep, ...]
    final: (
        str  # the status the claim must end in: draft, submitted, approved, rejected or withdrawn
    )


CLAIMS: tuple[ClaimSpec, ...] = (
    ClaimSpec(
        title="Team offsite travel",
        claimant=LENA,
        lines=(
            ClaimLine("travel", 9, "214.80", "Train tickets for the offsite"),
            ClaimLine("meals", 10, "96.50", "Team dinner on arrival"),
        ),
        steps=(),
        final="draft",
    ),
    ClaimSpec(
        title="Office supplies",
        claimant=LENA,
        lines=(ClaimLine("office", 11, "180.00", "Printer paper and toner for the office"),),
        steps=(ClaimStep(LENA, "submit"), ClaimStep(DEV_ADMIN, "approve")),
        final="approved",
    ),
    ClaimSpec(
        title="Design tool renewals",
        claimant=MARCUS,
        lines=(
            ClaimLine("software", 12, "189.00", "Annual plugin renewal"),
            ClaimLine("equipment", 14, "74.99", "Replacement stylus tips"),
        ),
        steps=(ClaimStep(MARCUS, "submit"),),
        final="submitted",
    ),
    ClaimSpec(
        title="Trade show travel",
        claimant=MARCUS,
        lines=(
            ClaimLine("travel", 15, "312.40", "Train and taxi to the trade show"),
            ClaimLine("meals", 16, "118.75", "Working lunch with the stand team"),
        ),
        steps=(ClaimStep(MARCUS, "submit"), ClaimStep(PRIYA, "approve")),
        final="approved",
    ),
    ClaimSpec(
        title="Client lunch",
        claimant=MARCUS,
        lines=(ClaimLine("meals", 11, "186.20", "Lunch with a prospective client"),),
        steps=(
            ClaimStep(MARCUS, "submit"),
            ClaimStep(
                PRIYA,
                "reject",
                "The receipt does not list the attendees. Add their names and submit again.",
            ),
        ),
        final="rejected",
    ),
    ClaimSpec(
        title="Replacement keyboards",
        claimant=MARCUS,
        lines=(ClaimLine("equipment", 18, "260.00", "Two replacement keyboards for the studio"),),
        steps=(ClaimStep(MARCUS, "submit"), ClaimStep(MARCUS, "withdraw")),
        final="withdrawn",
    ),
    ClaimSpec(
        title="Render farm hardware",
        claimant=MARCUS,
        lines=(ClaimLine("equipment", 20, "1240.00", "Memory and storage for the render farm"),),
        steps=(ClaimStep(MARCUS, "submit"), ClaimStep(PRIYA, "approve")),
        final="submitted",
    ),
    ClaimSpec(
        title="Launch event venue",
        claimant=MARCUS,
        lines=(
            ClaimLine("other", 22, "780.00", "Venue deposit for the launch evening"),
            ClaimLine("travel", 23, "210.00", "Ride hire for the launch crew"),
        ),
        steps=(
            ClaimStep(MARCUS, "submit"),
            ClaimStep(PRIYA, "approve"),
            ClaimStep(LENA, "approve"),
        ),
        final="approved",
    ),
)


@dataclass(frozen=True)
class DemoSummary:
    budget_lines: int
    months: int
    claim_statuses: dict[str, str]
    awaiting_second_approval: frozenset[str]
    collections: tuple[str, ...]


@dataclass
class DemoIdentity:
    """Signs people in without a network. The seed sets whom the next sign-in is for.

    It has the shape of the fake identity provider the tests use, and the seed runs only in dev.
    """

    claims: IdentityClaims | None = None
    nonces: set[str] = field(default_factory=set[str])

    async def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str
    ) -> str:
        self.nonces.add(nonce)
        return f"https://identity.demo.invalid/authorize?state={state}"

    async def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        if code != GOOD_CODE:
            raise IdentityRejected("refused")
        return TokenSet(id_token=GOOD_CODE)

    async def verify_id_token(self, id_token: str, *, nonce: str) -> IdentityClaims:
        if nonce not in self.nonces or self.claims is None:
            raise IdentityRejected("nonce mismatch")
        return self.claims


class _PrepareQueue:
    """Records the batches the routes ask to prepare. The seed runs them, as the worker would."""

    def __init__(self) -> None:
        self.pending: list[uuid.UUID] = []

    async def enqueue_prepare(self, batch_id: uuid.UUID) -> None:
        self.pending.append(batch_id)


class _Api:
    """Calls the routes as one persona at a time. An unexpected answer stops the seed."""

    def __init__(self, client: AsyncClient, identity: DemoIdentity, queue: _PrepareQueue) -> None:
        self._client = client
        self._identity = identity
        self._queue = queue
        self._signed_in: str | None = None

    async def sign_in(self, subject: str) -> None:
        if self._signed_in == subject:
            return
        self._identity.claims = IdentityClaims(subject=subject, email=subject, name=subject)
        started = await self._client.get(
            "/auth/login", params={"return_to": "/"}, follow_redirects=False
        )
        _expect(started, 302, f"start sign in as {subject}")
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        finished = await self._client.get(
            "/auth/callback", params={"code": GOOD_CODE, "state": state}, follow_redirects=False
        )
        _expect(finished, 302, f"finish sign in as {subject}")
        self._signed_in = subject

    async def get(self, path: str, **params: Any) -> Any:
        response = await self._client.get(path, params=params)
        return _expect(response, 200, f"GET {path}").json()

    async def post(
        self, path: str, *, json: Any = None, files: Any = None, expected: int = 200
    ) -> Any:
        response = await self._client.post(path, json=json, files=files)
        return _expect(response, expected, f"POST {path}").json()

    async def put(self, path: str, *, json: Any) -> Any:
        response = await self._client.put(path, json=json)
        return _expect(response, 200, f"PUT {path}").json()

    async def run_prepared_batches(self, embedder: Embedder) -> None:
        for batch_id in self._queue.pending:
            await run_prepare_job(batch_id, embedder=embedder)
        self._queue.pending.clear()


def _expect(response: Response, status: int, step: str) -> Response:
    if response.status_code != status:
        raise DemoSeedError(f"Could not {step}: HTTP {response.status_code} {response.text}")
    return response


async def seed_demo(
    app: FastAPI,
    *,
    identity: DemoIdentity,
    embedder: Embedder,
    today: date | None = None,
) -> DemoSummary:
    """Load the personas, then the demo data, into the database that the app is configured for."""
    today = today or datetime.now(UTC).date()
    await seed()

    queue = _PrepareQueue()
    app.dependency_overrides[get_identity_provider] = lambda: identity
    app.dependency_overrides[get_batch_queue] = lambda: queue
    app.dependency_overrides[get_embedder] = lambda: embedder
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        api = _Api(client, identity, queue)
        months = _last_twelve_months(today)
        departments = await _department_ids(api)
        budget_lines = await _budgets(api, departments, months)
        await _close_months(api, months[:-2])
        statuses, awaiting = await _claims(api, months[-2])
        collections = await _knowledge(api, embedder)
        await _check_search(api)
    return DemoSummary(
        budget_lines=budget_lines,
        months=len(months),
        claim_statuses=statuses,
        awaiting_second_approval=awaiting,
        collections=collections,
    )


def _last_twelve_months(today: date) -> list[str]:
    """YYYY-MM for the last twelve months, oldest first, ending in the current month."""
    result: list[str] = []
    year, month = today.year, today.month
    for _ in range(12):
        result.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(result))


async def _department_ids(api: _Api) -> dict[str, str]:
    await api.sign_in(LENA)
    rows: list[dict[str, Any]] = await api.get("/api/users/departments")
    ids = {row["code"]: str(row["id"]) for row in rows}
    missing = [code for code in DEPARTMENT_CODES if code not in ids]
    if missing:
        raise DemoSeedError(f"The departments are missing: {', '.join(missing)}.")
    return ids


async def _budgets(api: _Api, departments: Mapping[str, str], months: Sequence[str]) -> int:
    """One budget line per month, department and category. One history line is replaced once."""
    await api.sign_in(LENA)
    replaced_month = months[-4]
    count = 0
    for year_month in months:
        existing: dict[tuple[str, str], dict[str, Any]] = {}
        for line in await api.get("/api/budgets/lines", month=year_month):
            existing[(line["department_code"], line["category"])] = line
        for department in DEPARTMENT_CODES:
            for category in CATEGORIES:
                count += 1
                key = (department, category)
                line = existing.get(key)
                if line is None:
                    line = await api.post(
                        "/api/budgets/lines",
                        json={
                            "department_id": departments[department],
                            "year_month": year_month,
                            "category": category,
                            "amount": f"{Decimal(MONTHLY_BASE[department][category]):.2f}",
                        },
                        expected=201,
                    )
                is_replaced = key == (REPLACED_DEPARTMENT, REPLACED_CATEGORY)
                if is_replaced and year_month == replaced_month and line["version"] == 1:
                    _ = await api.put(
                        f"/api/budgets/lines/{line['line_id']}",
                        json={
                            "version": line["version"],
                            "amount": f"{REPLACED_AMOUNT:.2f}",
                            "reason": REPLACED_REASON,
                        },
                    )
    return count


async def _close_months(api: _Api, months: Sequence[str]) -> None:
    """Close every month that is passed in. The caller passes all but the last two months."""
    await api.sign_in(LENA)
    closed = {row["year_month"] for row in await api.get("/api/budgets/months")}
    for year_month in months:
        if year_month in closed:
            continue
        _ = await api.post(
            f"/api/budgets/months/{year_month}/close",
            json={"reason": "Closed after the claims were final."},
            expected=201,
        )


async def _claims(api: _Api, claim_month: str) -> tuple[dict[str, str], frozenset[str]]:
    """Make each demo claim once, then check that it reached the state the spec describes."""
    await api.sign_in(LENA)
    existing = {str(row["title"]): str(row["id"]) for row in await api.get("/api/claims")}
    claim_ids: dict[str, str] = {}
    for spec in CLAIMS:
        claim_id = existing.get(spec.title)
        if claim_id is None:
            claim_id = await _create_claim(api, spec, claim_month)
        claim_ids[spec.title] = claim_id

    statuses: dict[str, str] = {}
    awaiting: set[str] = set()
    await api.sign_in(LENA)
    for spec in CLAIMS:
        detail = await api.get(f"/api/claims/{claim_ids[spec.title]}")
        status = str(detail["claim"]["status"])
        if status != spec.final:
            raise DemoSeedError(
                f"The claim '{spec.title}' is {status}, not {spec.final}. "
                "Run `just reset` to rebuild the demo data."
            )
        statuses[spec.title] = status
        if status == "submitted" and _has_decided_step(detail):
            awaiting.add(spec.title)
    return statuses, frozenset(awaiting)


def _has_decided_step(detail: Mapping[str, Any]) -> bool:
    """True when the open approval run already has a decision, so it waits on a later step."""
    for run in detail["history"]:
        if run["state"] == "pending":
            return any(step["decision"] is not None for step in run["steps"])
    return False


async def _create_claim(api: _Api, spec: ClaimSpec, claim_month: str) -> str:
    await api.sign_in(spec.claimant)
    created = await api.post("/api/claims", json={"title": spec.title}, expected=201)
    claim_id = str(created["claim"]["id"])
    version = int(created["claim"]["version"])
    for index, line in enumerate(spec.lines, start=1):
        receipt = await _upload_receipt(api, spec, index, line)
        spent_on = date.fromisoformat(f"{claim_month}-{line.day:02d}")
        detail = await api.post(
            f"/api/claims/{claim_id}/lines",
            json={
                "expected_version": version,
                "category": line.category,
                "spent_on": spent_on.isoformat(),
                "amount": line.amount,
                "description": line.description,
                "receipt_sha256": receipt,
            },
            expected=201,
        )
        version = int(detail["claim"]["version"])
    for step in spec.steps:
        await api.sign_in(step.actor)
        if step.action in ("submit", "withdraw"):
            detail = await api.post(
                f"/api/claims/{claim_id}/{step.action}", json={"expected_version": version}
            )
        else:
            detail = await api.post(
                f"/api/claims/{claim_id}/decision",
                json={"expected_version": version, "decision": step.action, "reason": step.reason},
            )
        version = int(detail["claim"]["version"])
    return claim_id


async def _upload_receipt(api: _Api, spec: ClaimSpec, index: int, line: ClaimLine) -> str:
    text = f"Receipt for {spec.title}\nDescription: {line.description}\nAmount: USD {line.amount}\n"
    uploaded = await api.post(
        "/api/files",
        files={"file": (f"receipt-{index}.txt", text.encode("utf-8"), "text/plain")},
    )
    return str(uploaded["sha256"])


async def _knowledge(api: _Api, embedder: Embedder) -> tuple[str, ...]:
    policy_files = sorted(POLICY_DIR.glob("*.md"))
    if len(policy_files) != 9:
        raise DemoSeedError(
            f"Expected nine policy documents in {POLICY_DIR}, found {len(policy_files)}."
        )
    expense = {
        path.name: path.read_bytes()
        for path in policy_files
        if int(path.name[:2]) <= EXPENSE_FILE_LIMIT
    }
    finance = {
        path.name: path.read_bytes()
        for path in policy_files
        if int(path.name[:2]) > EXPENSE_FILE_LIMIT
    }
    await _load_collection(api, embedder, EXPENSE_COLLECTION, EXPENSE_RULES, expense)
    await _load_collection(api, embedder, FINANCE_COLLECTION, FINANCE_RULES, finance)
    return (EXPENSE_COLLECTION, FINANCE_COLLECTION)


async def _load_collection(
    api: _Api,
    embedder: Embedder,
    name: str,
    rules: Sequence[Mapping[str, object]],
    files: Mapping[str, bytes],
) -> None:
    """Create the collection as the admin, start a batch as finance, and apply it as the admin."""
    await api.sign_in(DEV_ADMIN)
    collections: list[dict[str, Any]] = await api.get("/api/knowledge/collections")
    found = next((row for row in collections if row["name"] == name), None)
    if found is None:
        found = await api.post(
            "/api/knowledge/collections",
            json={"name": name, "rules": [dict(rule) for rule in rules]},
            expected=201,
        )
    collection_id = str(found["id"])
    batches: list[dict[str, Any]] = await api.get("/api/knowledge/batches")
    if any(row["collection_name"] == name and row["state"] == "applied" for row in batches):
        return
    await api.sign_in(LENA)
    await _withdraw_open_batches(api, name, embedder)
    batch = await api.post(
        f"/api/knowledge/collections/{collection_id}/batches",
        json={"mode": "full_set"},
        expected=201,
    )
    batch_id = str(batch["id"])
    for file_name, data in files.items():
        _ = await api.post(
            f"/api/knowledge/batches/{batch_id}/files",
            files={"file": (file_name, data, "text/markdown")},
            expected=201,
        )
    _ = await api.post(f"/api/knowledge/batches/{batch_id}/prepare")
    await api.run_prepared_batches(embedder)
    submitted = await api.post(f"/api/knowledge/batches/{batch_id}/submit")
    if submitted["state"] != "awaiting_approval":
        raise DemoSeedError(
            f"The batch for '{name}' is {submitted['state']}, not awaiting approval."
        )
    await api.sign_in(DEV_ADMIN)
    approved = await api.post(f"/api/knowledge/batches/{batch_id}/approve", json={"reason": None})
    if approved["state"] != "applied":
        raise DemoSeedError(f"The batch for '{name}' is {approved['state']}, not applied.")


async def _withdraw_open_batches(api: _Api, name: str, embedder: Embedder) -> None:
    """Clear the open batches that a stopped run left in a collection, so a new batch can start.

    The API allows one open batch per collection. Only the worker moves a batch out of
    'preparing', and the queue the seed uses went away with the stopped run, so the seed runs
    that step itself. Then it cancels whatever is still open.
    """
    open_states = {state.value for state in OPEN_STATES}

    async def stranded() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = await api.get("/api/knowledge/batches")
        return [
            row for row in rows if row["collection_name"] == name and row["state"] in open_states
        ]

    for row in await stranded():
        if row["state"] == BatchState.PREPARING.value:
            await run_prepare_job(uuid.UUID(row["id"]), embedder=embedder)
    for row in await stranded():
        _ = await api.post(
            f"/api/knowledge/batches/{row['id']}/cancel",
            json={"reason": "A stopped demo run left this batch open."},
        )


async def _check_search(api: _Api) -> None:
    """Search as an employee and as finance, and stop the seed if access breaks the rules."""
    await api.sign_in(MARCUS)
    expense = await api.post("/api/knowledge/search", json={"query": "receipts for claim lines"})
    if EXPENSE_COLLECTION not in {hit["collection_name"] for hit in expense["results"]}:
        raise DemoSeedError("An employee's search did not find the expense policy.")
    finance = await api.post("/api/knowledge/search", json={"query": "month close procedure"})
    if FINANCE_COLLECTION in {hit["collection_name"] for hit in finance["results"]}:
        raise DemoSeedError("An employee's search reached the finance procedures.")
    await api.sign_in(LENA)
    as_finance = await api.post("/api/knowledge/search", json={"query": "month close procedure"})
    if FINANCE_COLLECTION not in {hit["collection_name"] for hit in as_finance["results"]}:
        raise DemoSeedError("Finance's search did not find the finance procedures.")


#: Tables that record each visit (a sign-in, a search). Every run adds rows to them, by design.
VISIT_TABLES = frozenset({"users.web_session", "knowledge.search_log"})


async def table_counts() -> dict[str, int]:
    """Row count of every table except the visit tables. The idempotency test compares two runs."""
    counts: dict[str, int] = {}
    async with session_factory(get_settings().database_url)() as session:
        for module in MODULES:
            for table in module.metadata.sorted_tables:
                if table.fullname in VISIT_TABLES:
                    continue
                counted = await session.scalar(select(func.count()).select_from(table))
                counts[table.fullname] = int(counted or 0)
    return counts


async def main() -> None:
    settings = get_settings()
    if settings.environment != "dev":
        raise SystemExit("The demo data loads only in the dev environment.")
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    summary = await seed_demo(app, identity=DemoIdentity(), embedder=get_embedder(settings))
    logger.info(
        "Demo data ready: %d months of budgets, %d claims, collections %s.",
        summary.months,
        len(summary.claim_statuses),
        ", ".join(summary.collections),
    )


if __name__ == "__main__":
    asyncio.run(main())

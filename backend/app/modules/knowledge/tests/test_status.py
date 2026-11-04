import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.users import CurrentPerson, current_person
from app.core.users.scope import DataScope
from app.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_status_reports_ok_for_a_viewer(migrated_database: None) -> None:
    department = uuid.uuid4()
    viewer = CurrentPerson(
        id=uuid.uuid4(),
        name="Viewer",
        email="viewer@example.test",
        department_id=department,
        department_code="test",
        department_name="Test",
        roles=frozenset(),
        permissions=frozenset({"knowledge.collection.view"}),
        scope=DataScope(all_departments=False, department_ids=frozenset({department})),
    )
    app.dependency_overrides[current_person] = lambda: viewer
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/knowledge/status")
    finally:
        _ = app.dependency_overrides.pop(current_person)
    assert response.status_code == 200
    assert response.json() == {"module": "knowledge", "status": "ok", "database": "ok"}

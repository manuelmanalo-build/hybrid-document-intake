# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false
from pathlib import Path

from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).parents[1]


def test_dashboard_home_imports_and_exposes_all_operations_pages() -> None:
    app = AppTest.from_file(str(DASHBOARD / "app.py")).run()

    assert not app.exception
    assert app.title[0].value == "Document Intake Operations"
    assert len(list((DASHBOARD / "pages").glob("*.py"))) == 10
    assert len(app.info) == 1


def test_overview_requires_a_fastapi_token_without_database_access() -> None:
    app = AppTest.from_file(str(DASHBOARD / "pages" / "1_Overview.py")).run()

    assert not app.exception
    assert app.title[0].value == "Overview"
    assert any("FastAPI token" in message.value for message in app.info)

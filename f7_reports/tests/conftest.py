"""F7 test fixtures: a SQLite database with the schema from F7's own tables."""
import pytest

from observa_reports.tables import metadata

from f7_helpers import sqlite_engine


@pytest.fixture()
def engine(tmp_path):
    eng = sqlite_engine(tmp_path / "reports.sqlite3")
    metadata.create_all(eng)
    yield eng
    eng.dispose()

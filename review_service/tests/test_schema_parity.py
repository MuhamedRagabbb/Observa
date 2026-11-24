"""F7's SQLAlchemy tables (used by its own tests) must match the migrated schema the service runs on."""
import pytest
from sqlalchemy import inspect

from observa_reports.tables import metadata
from observa_review_service.db import make_engine, upgrade


@pytest.fixture()
def migrated(tmp_path):
    url = f"sqlite:///{(tmp_path / 'parity.sqlite3').as_posix()}"
    upgrade(url)
    engine = make_engine(url)
    yield inspect(engine)
    engine.dispose()


@pytest.mark.parametrize("table", sorted(metadata.tables))
def test_f7_table_matches_migration(migrated, table):
    declared = metadata.tables[table]
    columns = {c["name"]: c for c in migrated.get_columns(table)}
    assert set(columns) == {c.name for c in declared.columns}
    for column in declared.columns:
        assert columns[column.name]["nullable"] == column.nullable, column.name
    assert set(migrated.get_pk_constraint(table)["constrained_columns"]) == \
        {c.name for c in declared.primary_key.columns}
    migrated_fks = {(tuple(fk["constrained_columns"]), fk["referred_table"]) for fk in migrated.get_foreign_keys(table)}
    declared_fks = {(tuple(fk.parent.name for fk in c.elements), c.referred_table.name)
                    for c in declared.foreign_key_constraints}
    assert declared_fks <= migrated_fks

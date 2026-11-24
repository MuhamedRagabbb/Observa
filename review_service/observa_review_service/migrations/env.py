"""Alembic environment. The URL comes from the Config set by db.upgrade()."""
from alembic import context

from observa_review_service.db import make_engine

config = context.config
URL = context.get_x_argument(as_dictionary=True).get("url") or config.get_main_option("sqlalchemy.url")


def run_migrations_online() -> None:
    engine = make_engine(URL)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    context.configure(url=URL, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()

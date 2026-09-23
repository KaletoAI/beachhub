from logging.config import fileConfig

from alembic import context
from beachhub_portal.config import settings
from beachhub_portal.models import SCHEMA, Base
from sqlalchemy import engine_from_config, pool, text

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name is not None:
    # disable_existing_loggers=False: Sonst schaltet Alembic beim Import in Tests die Logger der
    # App stumm (bekanntes Problem, siehe core/alembic/env.py-Pendant).
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=True,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()

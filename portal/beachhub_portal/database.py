from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from beachhub_portal.config import settings

engine: Engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def stelle_schema_sicher(target: Engine) -> None:
    with target.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS spiegel"))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

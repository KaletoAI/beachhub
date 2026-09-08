from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from beachhub_core.config import settings

# client_encoding=utf8 wird explizit erzwungen: auf manchen (insbesondere lokalen) Postgres-
# Clustern mit initdb-Locale "C" ist die Server-Encoding SQL_ASCII, wodurch psycopg3 rohe
# Bytes statt str liefert. Mit erzwungenem UTF-8-Client bleibt Kodieren/Dekodieren symmetrisch
# und verlustfrei; gegen einen regulär UTF8-kodierten Server (CI, Docker-Image) ist das ein No-Op.
engine: Engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args={"client_encoding": "utf8"},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def stelle_extensions_sicher(target: Engine) -> None:
    """btree_gist wird für Exklusionsconstraints über (uuid, tstzrange) gebraucht."""
    with target.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

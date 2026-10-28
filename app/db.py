from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей."""


settings = get_settings()

# pool_pre_ping отбрасывает разорванные соединения после перезапуска PostgreSQL.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Предоставляет транзакционную сессию на время HTTP-запроса."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


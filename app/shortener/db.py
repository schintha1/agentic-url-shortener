from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def get_engine(url: str) -> Engine:
    """Create an Engine; SQLite uses StaticPool for TestClient thread sharing."""

    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if ":memory:" in url:
            engine = create_engine(url, connect_args=connect_args, poolclass=StaticPool)
        else:
            engine = create_engine(url, connect_args=connect_args)
            with engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                conn.commit()
        return engine
    return create_engine(url)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

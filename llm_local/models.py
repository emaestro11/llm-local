"""SQLAlchemy models for candles, decisions, and replay runs."""

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    ForeignKey,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(5), nullable=False)
    timestamp_ms = Column(Integer, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    # Pre-computed indicators (nullable for warmup period)
    sma_24 = Column(Float, nullable=True)
    sma_96 = Column(Float, nullable=True)
    rsi_14 = Column(Float, nullable=True)
    macd = Column(Float, nullable=True)
    macd_signal = Column(Float, nullable=True)
    bb_upper = Column(Float, nullable=True)
    bb_lower = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp_ms", name="uq_candle"),
    )


class ReplayRun(Base):
    __tablename__ = "replay_runs"

    id = Column(Integer, primary_key=True)
    started_at = Column(String(30), nullable=False)
    completed_at = Column(String(30), nullable=True)
    status = Column(String(20), nullable=False, default="running")
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(5), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    config = Column(Text, nullable=False)
    candle_count = Column(Integer, nullable=True)
    decision_count = Column(Integer, nullable=True)
    quick_mode = Column(Integer, default=0)


class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("replay_runs.id"), nullable=False)
    candle_id = Column(Integer, ForeignKey("candles.id"), nullable=False)
    action = Column(String(10), nullable=False)
    size_pct = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    raw_response = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    is_fallback = Column(Integer, default=0)
    created_at = Column(String(30), nullable=False)


def init_db(db_path: str):
    """Create engine and ensure all tables exist."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def get_session(engine):
    """Context manager for database sessions."""
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

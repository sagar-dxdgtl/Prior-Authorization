from __future__ import annotations
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from ..config import get_settings

class Base(DeclarativeBase):
    pass

_app_engine = None
_owner_engine = None

def app_engine():
    global _app_engine
    if _app_engine is None:
        _app_engine = create_engine(get_settings().effective_app_db_url, pool_pre_ping=True, future=True)
    return _app_engine

def owner_engine():
    global _owner_engine
    if _owner_engine is None:
        _owner_engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _owner_engine

SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False, future=True)

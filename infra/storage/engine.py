import json
from sqlalchemy import create_engine, Engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine

_sync_engine: Engine | None = None
_async_engine: AsyncEngine | None = None


def _custom_json_serializer(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def get_engine() -> Engine:
    global _sync_engine
    if _sync_engine is None:
        from configs.settings import get_settings

        s = get_settings()
        _sync_engine = create_engine(
            s.database.url,
            pool_size=s.database.pool_size,
            max_overflow=s.database.max_overflow,
            pool_pre_ping=True,  # drop stale connections before use
            pool_recycle=3600,  # recycle connections every hour
            echo=s.database.echo,
            json_serializer=_custom_json_serializer,
            json_deserializer=json.loads,
        )
    return _sync_engine


def get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        from configs.settings import get_settings

        s = get_settings()
        _async_engine = create_async_engine(
            s.database.async_url,
            pool_size=s.database.pool_size,
            max_overflow=s.database.max_overflow,
            pool_pre_ping=True,  # drop stale connections before use
            pool_recycle=3600,  # recycle connections every hour
            echo=s.database.echo,
            json_serializer=_custom_json_serializer,
            json_deserializer=json.loads,
        )
    return _async_engine


def close_engines():
    global _sync_engine, _async_engine
    if _sync_engine:
        _sync_engine.dispose()
        _sync_engine = None
    if _async_engine:
        import asyncio

        asyncio.get_event_loop().run_until_complete(_async_engine.dispose())
        _async_engine = None

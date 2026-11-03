from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def checkpoint_dsn(database_url: str) -> str:
    """将 SQLAlchemy URL 转换为 psycopg 连接串。"""

    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


class PostgresCheckpointRuntime:
    """管理 FastAPI 进程内的异步连接池与 LangGraph checkpointer。"""

    def __init__(self, database_url: str) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=checkpoint_dsn(database_url),
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self.saver = AsyncPostgresSaver(self._pool)

    async def start(self) -> AsyncPostgresSaver:
        await self._pool.open()
        await self.saver.setup()
        return self.saver

    async def close(self) -> None:
        await self._pool.close()

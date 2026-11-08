"""Postgres access (psycopg 3). Uses a small connection pool when psycopg_pool is
installed, so the API's concurrent requests each get their own connection.

    pip install "psycopg[binary,pool]"
"""
from __future__ import annotations

import os


class Database:
    def __init__(self, dsn: str | None = None, max_size: int = 5):
        from psycopg.rows import dict_row

        dsn = dsn or os.environ["DATABASE_URL"]
        kwargs = {"autocommit": True, "row_factory": dict_row}
        try:
            from psycopg_pool import ConnectionPool
            self.pool = ConnectionPool(dsn, min_size=1, max_size=max_size, kwargs=kwargs, open=True)
            self.conn = None
        except ImportError:
            import psycopg
            self.pool, self.conn = None, psycopg.connect(dsn, **kwargs)

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        if self.pool is None:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if cur.description else []
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def run_file(self, path: str) -> None:
        sql = open(path).read()
        if self.pool is None:
            self.conn.execute(sql)
        else:
            with self.pool.connection() as conn:
                conn.execute(sql)
